"""SparkX referee / mission server (organizer side).

Subscribes (private):  /sparkx/referee_private/gt_odom, /sparkx/referee_private/bumper
Publishes  (private):  /sparkx/referee_private/cmd_vel  (gated team commands)
Team API:  /cmd_vel (in), /sparkx/mission/{status,events,hints} (out),
           services /sparkx/mission/{get_info,get_hints,report_survivor,submit_record,end},
           recorded team outputs /map, /team/semantic_map, /team/clues, /team/behavior_state
Organizer: /sparkx/admin/{start,pause,intervention,abort} (std_srvs/Trigger), web dashboard.

Lock-down (see docs/HOSTING.md):
  * stack integrity - the locked files of the stack are hashed against LOCKED_MANIFEST.sha256 when
    the referee starts; the result goes to the log, the dashboard and summary.json. With
    require_integrity:=true the mission cannot be started on a modified / unverifiable stack.
  * private-topic tripwire - once per second the ROS graph is inspected; any endpoint on
    /sparkx/referee_private/* that is not the referee or its bridge is a RULE_VIOLATION
    (violation_policy: warn | disqualify).
  * competition:=true turns both on and removes the unauthenticated ROS admin services (the
    dashboard with its admin token stays).
"""
import json
import math
import os
import subprocess
import threading
import time

import rclpy
import tf2_ros
from geometry_msgs.msg import Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from ros_gz_interfaces.msg import Contacts
from sparkx_interfaces.msg import (BehaviorState, ClueArray, Hint, LandmarkRef, MissionEvent, MissionStatus,
                                   SemanticMap)
from sparkx_interfaces.srv import EndMission, GetHints, GetMissionInfo, ReportSurvivor, SubmitMissionRecord
from std_srvs.srv import Trigger

from sparkx_worlds import vocab

from . import evaluation as EV
from . import integrity
from . import rules as R
from .core import CODES, FINISHED, RUNNING, Referee


PRIVATE_PREFIX = '/sparkx/referee_private/'
BRIDGE_NODE = 'sparkx_referee_bridge'
# the only endpoints that may exist on the private topics: (node, action, topic suffix)
PRIVATE_ENDPOINTS = {
    ('sparkx_referee', 'publishes', 'cmd_vel'), ('sparkx_referee', 'subscribes', 'gt_odom'),
    ('sparkx_referee', 'subscribes', 'bumper'), (BRIDGE_NODE, 'publishes', 'gt_odom'),
    (BRIDGE_NODE, 'publishes', 'bumper'), (BRIDGE_NODE, 'subscribes', 'cmd_vel'),
}


def yaw_q(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class RefereeNode(Node):
    def __init__(self):
        super().__init__('sparkx_referee')
        p = self.declare_parameter
        world_dir = p('world_dir', '').value
        if not world_dir:
            from ament_index_python.packages import get_package_share_directory
            world_dir = os.path.join(get_package_share_directory('sparkx_worlds'), 'worlds',
                                     p('world', 'practice_small_day').value)
        self.world_dir = os.path.abspath(os.path.expanduser(world_dir))
        self.team = p('team_name', 'team').value
        self.auto_start = p('auto_start', True).value
        self.start_delay = p('start_delay_s', 5.0).value
        self.max_v = p('max_linear_speed', 1.0).value
        self.max_w = p('max_angular_speed', 1.5).value
        self.cmd_timeout = p('cmd_timeout_s', 0.5).value
        log_root = os.path.expanduser(p('log_dir', '~/sparkx_logs').value)
        self.dashboard_port = p('dashboard_port', 8080).value
        self.enable_aftershocks = p('aftershocks', True).value
        # ---- lock-down
        self.competition = bool(p('competition', False).value)
        self.require_integrity = bool(p('require_integrity', False).value) or self.competition
        self.violation_policy = str(p('violation_policy', '').value or ('disqualify' if self.competition else 'warn'))
        if self.violation_policy not in ('warn', 'disqualify'):
            raise ValueError('violation_policy must be "warn" or "disqualify"')
        self.trusted_nodes = {n.strip() for n in str(p('trusted_nodes', '').value or '').split(',') if n.strip()}
        ros_admin = bool(p('ros_admin_services', True).value) and not (self.competition and self.dashboard_port > 0)
        stack_root = integrity.find_stack_root([p('stack_root', '').value])
        self.integrity = integrity.verify(stack_root)
        self.violations = []
        self.disqualified = False
        self._suspects = {}
        self.start_block = ''
        if self.require_integrity and self.integrity['status'] != 'ok':
            self.start_block = f'stack integrity is {self.integrity["status"].upper()}: {self.integrity["note"]}'
        self.ref = Referee(self.world_dir, self.team, p('hide_survivor_total', False).value)
        # organiser override of the world's time limit (0 = keep it): used to measure how long a
        # mission really takes before fixing the limit for an event
        from rcl_interfaces.msg import ParameterDescriptor
        tl = float(self.declare_parameter('time_limit_s', 0.0, ParameterDescriptor(dynamic_typing=True)).value or 0.0)
        if tl > 0:
            self.ref.time_limit = tl
            self.get_logger().warn(f'time limit overridden: {tl:.0f} s (the world file says {self.ref.sc["time_limit_s"]:.0f} s)')
        self.gz_world = self.ref.sc['world']
        stamp = time.strftime('%Y%m%d_%H%M%S')
        self.log_dir = os.path.join(log_root, f'{stamp}_{self.team}_{self.gz_world}')
        os.makedirs(os.path.join(self.log_dir, 'evidence'), exist_ok=True)
        self.event_log = open(os.path.join(self.log_dir, 'events.jsonl'), 'a')
        self.lock = threading.RLock()

        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                             reliability=ReliabilityPolicy.RELIABLE)
        self.pub_cmd = self.create_publisher(Twist, '/sparkx/referee_private/cmd_vel', 10)
        self.pub_status = self.create_publisher(MissionStatus, '/sparkx/mission/status', 10)
        self.pub_events = self.create_publisher(MissionEvent, '/sparkx/mission/events', 50)
        self.pub_hints = self.create_publisher(Hint, '/sparkx/mission/hints', QoSProfile(
            depth=50, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE))
        self.create_subscription(Twist, '/cmd_vel', self.on_cmd, 10)
        self.create_subscription(Odometry, '/sparkx/referee_private/gt_odom', self.on_gt, qos_profile_sensor_data)
        self.create_subscription(Contacts, '/sparkx/referee_private/bumper', self.on_bumper, qos_profile_sensor_data)
        self.last_map = None
        self.last_sem = None
        self.last_clues = None
        self.last_behavior = None
        self.create_subscription(OccupancyGrid, '/map', lambda m: setattr(self, 'last_map', m), latched)
        self.create_subscription(SemanticMap, '/team/semantic_map', lambda m: setattr(self, 'last_sem', m), 10)
        self.create_subscription(ClueArray, '/team/clues', lambda m: setattr(self, 'last_clues', m), 10)
        self.create_subscription(BehaviorState, '/team/behavior_state', self.on_behavior, 10)
        self.create_service(GetMissionInfo, '/sparkx/mission/get_info', self.srv_info)
        self.create_service(GetHints, '/sparkx/mission/get_hints', self.srv_hints)
        self.create_service(ReportSurvivor, '/sparkx/mission/report_survivor', self.srv_report)
        self.create_service(SubmitMissionRecord, '/sparkx/mission/submit_record', self.srv_submit)
        self.create_service(EndMission, '/sparkx/mission/end', self.srv_end)
        if ros_admin:   # anyone on the ROS graph can call these: practice only, the dashboard needs a token
            self.create_service(Trigger, '/sparkx/admin/start', lambda q, r: self.admin(r, self.request_start))
            self.create_service(Trigger, '/sparkx/admin/pause', lambda q, r: self.admin(r, self.ref.pause))
            self.create_service(Trigger, '/sparkx/admin/intervention',
                                lambda q, r: self.admin(r, lambda: self.ref.intervention('organizer intervention')))
            self.create_service(Trigger, '/sparkx/admin/abort',
                                lambda q, r: self.admin(r, lambda: self.ref.finish('ABORTED')))
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
        self.last_cmd = Twist()
        self.last_cmd_time = 0.0
        self.gt_twist = (0.0, 0.0)
        self.sim_t = 0.0
        self.t_created = time.time()
        self.finalized = False
        self.recent_evidence = []
        self.create_timer(0.05, self.tick)
        self.create_timer(0.5, self.publish_status)
        self.create_timer(1.0, self.sample_localization)
        # wall-clock timer: the tripwire must keep running while the simulation is paused or slow
        self.create_timer(float(p('graph_check_period_s', 1.0).value), self.check_graph,
                          clock=Clock(clock_type=ClockType.STEADY_TIME))
        # wall-clock watchdog: every other timer here runs on simulation time, so if /clock never
        # arrives (bridge not connected, simulator not running) the referee would wait in silence
        self._clock_seen = False
        self._clock_warned = 0
        self.create_timer(5.0, self.check_clock, clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.dashboard = None
        if self.dashboard_port > 0:
            from .dashboard import Dashboard
            self.dashboard = Dashboard(self, self.dashboard_port)
        self.get_logger().info(f'SparkX referee ready: world={self.gz_world} team={self.team} logs={self.log_dir}')
        self.report_integrity()
        self.get_logger().info(f'lock-down: competition={self.competition} require_integrity={self.require_integrity} '
                               f'violation_policy={self.violation_policy} ros_admin_services={ros_admin}')
        self.gz_ip = os.environ.get('GZ_IP', '')
        if self.competition and self.gz_ip != '127.0.0.1':
            self.get_logger().warning(
                'GZ_IP is not 127.0.0.1: Gazebo transport (every model pose) can be read from team containers on a '
                'Docker network. Start the simulator and the referee with GZ_IP=127.0.0.1 (docs/HOSTING.md).')

    # ---------------------------------------------------------------- lock-down
    def report_integrity(self):
        r = self.integrity
        head = f'stack integrity: {r["status"].upper()} ({r["files"]} locked files, fingerprint {r["digest"][:16] or "-"})'
        if r['status'] == 'ok':
            self.get_logger().info(head)
        else:
            self.get_logger().warning(f'{head} - {r["note"]}')
            for key in ('modified', 'added', 'removed'):
                for rel in r[key][:20]:
                    self.get_logger().warning(f'  {key}: {rel}')
        self.emit([self.note('STACK_INTEGRITY', f'{r["status"]}: {r["note"] or "locked files match the manifest"}')])
        if self.start_block:
            self.get_logger().error(f'MISSION START BLOCKED - {self.start_block}')

    def note(self, typ, text, ref_id=''):
        """Organiser-side event that is not a score item. It is only added to the mission event list
        once the mission has started (the core tells MISSION_STARTED from MISSION_RESUMED by whether
        that list is empty)."""
        e = dict(t=round(self.ref.elapsed, 2), wall=time.time(), type=typ, description=text, score_delta=0,
                 ref_id=ref_id)
        if self.ref.events:
            self.ref.events.append(e)
        return e

    def request_start(self):
        """Start / resume, unless the lock-down forbids running this stack."""
        if self.start_block:
            return [self.note('STACK_INTEGRITY', f'start refused - {self.start_block}')]
        return self.ref.start_mission()

    def is_trusted(self, name):
        """trusted_nodes entries are exact node names, or prefixes when they end with '*'."""
        return any(name == n or (n.endswith('*') and name.startswith(n[:-1])) for n in self.trusted_nodes)

    def private_endpoints(self):
        """[(node, action, topic)] of every endpoint on the private topics that should not exist."""
        bad = []
        for topic, _types in self.get_topic_names_and_types():
            if not topic.startswith(PRIVATE_PREFIX):
                continue
            suffix = topic[len(PRIVATE_PREFIX):]
            for action, infos in (('publishes', self.get_publishers_info_by_topic(topic)),
                                  ('subscribes', self.get_subscriptions_info_by_topic(topic))):
                seen = {}
                for i in infos:
                    seen[i.node_name] = seen.get(i.node_name, 0) + 1
                    full = (i.node_namespace.rstrip('/') + '/' + i.node_name)
                    expected = (i.node_name, action, suffix) in PRIVATE_ENDPOINTS and seen[i.node_name] == 1
                    if suffix == 'chase_cam' and i.node_name.startswith('_NODE_NAME_UNKNOWN') and \
                            self.is_trusted('sparkx_recorder'):
                        continue   # the organiser's own video recorder, still being discovered (no ground truth on this topic)
                    if not expected and not self.is_trusted(i.node_name):
                        bad.append((full, action, topic))
        return bad

    def check_graph(self):
        if self.ref.state == FINISHED:
            return
        try:
            now = set(self.private_endpoints())
        except Exception as e:  # graph queries can fail while nodes come and go
            self.get_logger().debug(f'graph check failed: {e}')
            return
        ev = []
        with self.lock:
            # an endpoint must be seen on two consecutive checks (discovery can report half-known nodes)
            self._suspects = {k: self._suspects.get(k, 0) + 1 for k in now}
            for (node, action, topic), n in sorted(self._suspects.items()):
                if n != 2:
                    continue
                text = f'{node} {action} on {topic}'
                self.violations.append(dict(t=round(self.ref.elapsed, 1), node=node, action=action, topic=topic))
                ev.append(self.note('RULE_VIOLATION', f'forbidden access to referee-private data: {text}', node))
                if self.violation_policy == 'disqualify' and not self.disqualified:
                    self.disqualified = True
                    ev += self.ref.finish(f'DISQUALIFIED: {text}')
        self.emit(ev)

    # ---------------------------------------------------------------- callbacks
    def emit(self, events):
        for e in events:
            m = MissionEvent()
            m.header.stamp = self.get_clock().now().to_msg()
            m.type, m.description, m.score_delta, m.ref_id = e['type'], e['description'], e['score_delta'], e['ref_id']
            self.pub_events.publish(m)
            self.event_log.write(json.dumps(e) + '\n')
            self.event_log.flush()
            line = f'[{e["t"]:7.1f}s] {e["type"]}: {e["description"]} ({e["score_delta"]:+d})'
            if e['score_delta'] < 0:
                self.get_logger().warning(line)
            else:
                self.get_logger().info(line)
            if e['type'] == 'MISSION_FINISHED':
                self.finalize_later()

    def check_clock(self):
        if self._clock_seen:
            return
        if self.get_clock().now().nanoseconds > 0 or self.ref.pose is not None:
            self._clock_seen = True
            if self._clock_warned:
                self.get_logger().info('simulation time is now arriving; the referee is live')
            return
        waited = time.time() - self.t_created
        if waited > 15.0:
            self._clock_warned += 1
            self.get_logger().error(
                f'NO SIMULATION TIME after {waited:.0f} s: nothing is arriving on /clock, so the mission cannot start. '
                'Check that the simulator is running and that the sparkx_team_bridge node bridges /clock '
                '(same ROS_DOMAIN_ID and GZ_PARTITION as this referee); relaunch the simulation if it does not recover.')

    def admin(self, resp, fn):
        with self.lock:
            ev = fn()
        self.emit(ev)
        resp.success = True
        resp.message = self.ref.status()['state_text']
        return resp

    def on_cmd(self, msg):
        self.last_cmd = msg
        self.last_cmd_time = time.monotonic()

    def on_gt(self, msg):
        self.sim_t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        p, q, tw = msg.pose.pose.position, msg.pose.pose.orientation, msg.twist.twist
        with self.lock:
            ev = self.ref.update(self.sim_t, (p.x, p.y, p.z, q.x, q.y, q.z, q.w),
                                 (math.hypot(tw.linear.x, tw.linear.y), tw.angular.z))
        self.emit(ev)

    def on_bumper(self, msg):
        ev = []
        with self.lock:
            for c in msg.contacts:
                a, b = c.collision1.name, c.collision2.name
                other = b if a.startswith('sparkx_ugv') else a
                ev += self.ref.on_contact(other)
        self.emit(ev)

    def on_behavior(self, msg):
        self.last_behavior = msg

    # ---------------------------------------------------------------- periodic
    def tick(self):
        if self.auto_start and self.ref.state == 0 and self.ref.pose is not None and \
                time.time() - self.t_created > self.start_delay:
            self.auto_start = False
            with self.lock:
                ev = self.request_start()
            self.emit(ev)
        out = Twist()
        if self.ref.cmd_enabled() and time.monotonic() - self.last_cmd_time < self.cmd_timeout:
            out.linear.x = max(-self.max_v, min(self.max_v, self.last_cmd.linear.x))
            out.angular.z = max(-self.max_w, min(self.max_w, self.last_cmd.angular.z))
        self.pub_cmd.publish(out)
        if self.enable_aftershocks:
            with self.lock:
                cand = self.ref.aftershock_due()
            if cand:
                self.spawn_aftershock(cand)

    def spawn_aftershock(self, cand):
        path = os.path.join(self.world_dir, 'private', cand['sdf'])
        sdf = open(path).read().replace('{WORLD_DIR}', self.world_dir)
        req = f'sdf: {json.dumps(sdf)}, name: "aftershock_{cand["id"]}", allow_renaming: true'
        with self.lock:
            ev = self.ref.apply_aftershock(cand)
        self.emit(ev)

        def run():
            r = subprocess.run(['gz', 'service', '-s', f'/world/{self.gz_world}/create', '--reqtype',
                                'gz.msgs.EntityFactory', '--reptype', 'gz.msgs.Boolean', '--timeout', '5000',
                                '--req', req], capture_output=True, text=True)
            self.get_logger().info(f'aftershock {cand["id"]} spawned at segment {cand["segment"]}: {r.stdout.strip()}')
        threading.Thread(target=run, daemon=True).start()

    def team_pose(self):
        try:
            t = self.tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time())
            tr = t.transform.translation
            return (tr.x, tr.y, yaw_q(t.transform.rotation))
        except Exception:
            return None

    def sample_localization(self):
        if self.ref.state != RUNNING or self.ref.pose is None:
            return
        tp = self.team_pose()
        if tp is not None:
            p = self.ref.pose['p']
            self.ref.team_pose_samples.append((self.ref.elapsed, (p.x, p.y), self.ref.map_to_world(tp[0], tp[1])))

    def publish_status(self):
        s = self.ref.status()
        m = MissionStatus()
        m.header.stamp = self.get_clock().now().to_msg()
        m.state, m.state_text, m.team_name, m.world_name = s['state'], s['state_text'], s['team'], s['world']
        m.elapsed_s, m.time_limit_s, m.remaining_s = float(s['elapsed']), float(s['time_limit']), float(s['remaining'])
        m.score = s['score']
        m.survivors_rescued, m.survivors_total, m.hints_released = s['rescued'], s['total'], s['hints']
        m.collisions, m.false_reports = s['collisions'], s['false_reports']
        m.late_reports, m.idle_penalties = s['late_reports'], s['idle_penalties']
        m.robot_health, m.cmd_vel_enabled, m.finish_reason = float(s['health']), s['cmd_enabled'], s['finish_reason']
        self.pub_status.publish(m)

    # ---------------------------------------------------------------- services
    def hint_msg(self, h):
        m = Hint()
        m.hint_id, m.source_survivor_id = h['id'], h['source']
        m.stamp = self.get_clock().now().to_msg()
        m.people_count = int(h.get('people_count', 1))
        m.relation = h['relation']
        m.landmark = LandmarkRef(landmark_class=h['landmark']['cls'], color=h['landmark'].get('color', ''),
                                 label=h['landmark'].get('label', ''))
        m.area_type, m.approx_distance_m, m.text = h.get('area_type', ''), float(h.get('approx_distance_m', 0)), h['text']
        mk = h.get('marker')
        m.marker_id = int(mk['id']) if mk else -1
        m.marker_bearing_deg = float(mk['bearing_deg']) if mk else 0.0
        m.marker_distance_m = float(mk['distance_m']) if mk else 0.0
        return m

    def srv_info(self, req, res):
        sc = self.ref.sc
        res.rules_version, res.world_name, res.team_name = R.RULES_VERSION, sc['world'], self.team
        res.time_limit_s = float(self.ref.time_limit)
        rs = sc['rescue']
        res.rescue_radius_m, res.stop_dwell_s = float(rs['radius_m']), float(rs['stop_dwell_s'])
        res.max_stop_speed_mps, res.report_tolerance_m = float(rs['max_stop_speed_mps']), float(rs['report_tolerance_m'])
        res.base_zone_radius_m = float(rs['base_zone_radius_m'])
        res.start_heading_enu_rad = float(sc['start']['yaw'])
        res.max_linear_speed_mps, res.max_angular_speed_rps = float(self.max_v), float(self.max_w)
        res.landmark_classes, res.colors = vocab.LANDMARK_CLASSES, vocab.COLORS
        res.relations, res.area_types = vocab.RELATIONS, vocab.AREA_TYPES
        res.marker_dictionary = sc.get('marker_dictionary', '') if sc.get('markers') else ''
        res.markers_total = len(sc.get('markers', []))
        res.marker_tag_size_m = 0.825
        res.report_window_s, res.idle_limit_s, res.idle_move_m = R.REPORT_WINDOW_S, R.IDLE_LIMIT_S, R.IDLE_MOVE_M
        return res

    def srv_hints(self, req, res):
        res.hints = [self.hint_msg(self.ref.hints[h]) for h in self.ref.released]
        return res

    def srv_report(self, req, res):
        frame = req.estimated_position.header.frame_id.lstrip('/') or 'map'
        with self.lock:
            code, msg, sid, pts, err, hints, ev = self.ref.report(
                frame, req.estimated_position.point.x, req.estimated_position.point.y, self.team_pose(),
                req.detection_source, req.confidence)
        self.emit(ev)
        res.success = code == 'CONFIRMED'
        res.result_code, res.message, res.survivor_id = CODES[code], f'{code}: {msg}', sid
        res.score_delta, res.position_error_m = int(pts), float(err)
        res.hints = [self.hint_msg(h) for h in hints]
        for h in res.hints:
            self.pub_hints.publish(h)
        # Logging must never cost the team its answer: a report whose evidence cannot be written is
        # still judged and answered (a service callback that raises sends no reply at all).
        try:
            os.makedirs(os.path.join(self.log_dir, 'evidence'), exist_ok=True)
            if req.evidence.data:
                fn = os.path.join(self.log_dir, 'evidence', f'{self.ref.elapsed:07.1f}_{code}_{sid or "none"}.'
                                  f'{req.evidence.format.split(";")[0] or "jpg"}')
                open(fn, 'wb').write(bytes(req.evidence.data))
            rec = dict(t=self.ref.elapsed, code=code, survivor=sid, frame=frame, x=req.estimated_position.point.x,
                       y=req.estimated_position.point.y, source=req.detection_source, confidence=req.confidence,
                       description=req.description)
            with open(os.path.join(self.log_dir, 'reports.jsonl'), 'a') as f:
                f.write(json.dumps(rec) + '\n')
        except OSError as e:
            self.get_logger().error(f'could not log the report to {self.log_dir}: {e}')
        return res

    def srv_submit(self, req, res):
        self.ref.submitted = dict(map=req.map, objects=[dict(object_class=o.object_class, color=o.color, label=o.label,
                                                             x=o.position.x, y=o.position.y, status=o.status,
                                                             id=o.id, survivor_id=o.survivor_id) for o in req.objects],
                                  clues=[dict(hint_id=c.hint_id, status=c.status, note=c.note) for c in req.clues],
                                  rescue_order=list(req.rescue_order), record_json=req.record_json)
        self.emit([self.ref.event('MAP_SUBMITTED', f'record submitted ({len(req.objects)} objects)')])
        res.success, res.message = True, 'record stored'
        return res

    def srv_end(self, req, res):
        with self.lock:
            ok, msg, in_base, score, ev = self.ref.end_mission(req.reason or 'COMPLETED')
        self.emit(ev)
        res.success, res.message, res.in_base_zone, res.final_score = ok, msg, in_base, int(score)
        return res

    # ---------------------------------------------------------------- final evaluation
    def finalize_later(self):
        threading.Timer(3.0, self.finalize).start()

    def finalize(self):
        if self.finalized:
            return
        self.finalized = True
        ref = self.ref
        sub = ref.submitted
        grid = sub['map'] if sub and sub['map'].info.width else self.last_map
        objs = sub['objects'] if sub else [dict(object_class=o.object_class, color=o.color, label=o.label,
                                                x=o.position.x, y=o.position.y, status=o.status)
                                           for o in (self.last_sem.objects if self.last_sem else [])]
        m2w = lambda x, y: ref.map_to_world(x, y)  # noqa: E731
        import numpy as np
        m2w_vec = lambda x, y: (ref.start.x + np.cos(ref.start.yaw) * x - np.sin(ref.start.yaw) * y,  # noqa: E731
                                ref.start.y + np.sin(ref.start.yaw) * x + np.cos(ref.start.yaw) * y)
        mq = EV.evaluate_map(grid, ref, m2w_vec)
        sem = EV.evaluate_semantics(objs, ref, m2w)
        loc = EV.localization_error(ref.team_pose_samples)
        with self.lock:
            ref.add(R.SCORING['map_quality_max'] * mq['score'], 'MAP_QUALITY', f'map score {mq["score"]:.2f}')
            ref.add(R.SCORING['semantic_max'] * sem['score'], 'SEMANTIC_RECORD', f'semantic score {sem["score"]:.2f}')
        integ = dict(self.integrity)
        for key in ('modified', 'added', 'removed'):
            integ[key] = integ[key][:100]
        summary = dict(team=self.team, world=ref.sc['world'], finish_reason=ref.finish_reason,
                       final_score=ref.score, official_score=0 if self.disqualified else ref.score,
                       disqualified=self.disqualified, rule_violations=self.violations, stack_integrity=integ,
                       competition=self.competition, violation_policy=self.violation_policy,
                       gz_ip=getattr(self, 'gz_ip', ''),
                       elapsed_s=round(ref.elapsed, 1), time_limit_s=ref.time_limit,
                       rescued=ref.rescue_order, survivors_total=len(ref.survivors),
                       hints_released=ref.released, collisions=ref.collisions, false_reports=ref.false_reports,
                       late_reports=ref.late_reports, idle_penalties=ref.idle_penalties, rules_version=R.RULES_VERSION,
                       health=round(ref.health, 1), map=mq, semantics=sem, localization=loc,
                       aftershocks=[a['id'] for a in ref.aftershocks_done],
                       clues=(sub or {}).get('clues', []),
                       score_breakdown=[dict(t=round(t, 1), points=round(p, 1), type=ty, text=tx)
                                        for t, p, ty, tx, _ in ref.score_items],
                       trajectory=[[round(v, 2) for v in r] for r in ref.trajectory])
        os.makedirs(self.log_dir, exist_ok=True)
        with open(os.path.join(self.log_dir, 'summary.json'), 'w') as f:
            json.dump(summary, f, indent=1)
        if grid is not None and grid.info.width:
            import numpy as np
            from PIL import Image
            d = np.asarray(grid.data, np.int16).reshape(grid.info.height, grid.info.width)
            img = np.where(d < 0, 205, np.where(d >= 65, 0, 254)).astype(np.uint8)
            Image.fromarray(img[::-1]).save(os.path.join(self.log_dir, 'team_map.png'))
        try:
            self.publish_status()
        except Exception:   # the ROS context is already gone when finalize runs from a Ctrl-C shutdown
            pass
        if self.disqualified:
            self.get_logger().error(f'DISQUALIFIED ({len(self.violations)} rule violation(s)) - official score 0')
        self.get_logger().info(f'FINAL SCORE {ref.score} | rescued {len(ref.rescue_order)}/{len(ref.survivors)} | '
                               f'map {mq["score"]:.2f} | semantic {sem["score"]:.2f} | loc RMSE {loc["rmse"]:.2f} m'
                               f' -> {self.log_dir}/summary.json')


def main():
    rclpy.init()
    node = RefereeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        if node.ref.state != FINISHED:
            node.ref.finish('SHUTDOWN')
        node.finalize()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
