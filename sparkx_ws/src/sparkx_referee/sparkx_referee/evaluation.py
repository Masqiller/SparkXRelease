"""End-of-mission evaluation: map quality vs ground truth, semantic record accuracy, trajectory stats."""
import math

import numpy as np


def evaluate_map(grid_msg, ref, map_to_world):
    """grid_msg: nav_msgs/OccupancyGrid (team map frame). Returns dict with IoU/coverage and score 0..1."""
    if grid_msg is None or grid_msg.info.width == 0:
        return dict(score=0.0, note='no map')
    info = grid_msg.info
    data = np.asarray(grid_msg.data, np.int16).reshape(info.height, info.width)
    oq = info.origin.orientation
    oyaw = math.atan2(2 * (oq.w * oq.z + oq.x * oq.y), 1 - 2 * (oq.y ** 2 + oq.z ** 2))
    jj, ii = np.nonzero(data >= 0)
    if len(jj) == 0:
        return dict(score=0.0, note='empty map')
    lx = (ii + 0.5) * info.resolution
    ly = (jj + 0.5) * info.resolution
    c, s = math.cos(oyaw), math.sin(oyaw)
    mx = info.origin.position.x + c * lx - s * ly
    my = info.origin.position.y + s * lx + c * ly
    wx, wy = map_to_world(mx, my)
    occ_team = data[jj, ii] >= 65
    gi = ((wx - ref.gt_origin[0]) / ref.gt_res).astype(int)
    gj = ((wy - ref.gt_origin[1]) / ref.gt_res).astype(int)
    ok = (gi >= 0) & (gj >= 0) & (gi < ref.gt_occ.shape[1]) & (gj < ref.gt_occ.shape[0])
    gi, gj, occ_team = gi[ok], gj[ok], occ_team[ok]
    from scipy.ndimage import binary_dilation
    gt_occ = ref.gt_occ.astype(bool)
    gt_occ_tol = binary_dilation(gt_occ, iterations=3)          # 0.3 m tolerance
    free_tol = ~binary_dilation(gt_occ, iterations=1)
    tp = int((occ_team & gt_occ_tol[gj, gi]).sum())
    fp = int((occ_team & ~gt_occ_tol[gj, gi]).sum())
    free_team = ~occ_team
    free_ok = int((free_team & free_tol[gj, gi]).sum())
    precision_occ = tp / max(1, tp + fp)
    precision_free = free_ok / max(1, int(free_team.sum()))
    reach = ref.gt_reach.astype(bool)
    seen = np.zeros_like(reach)
    seen[gj[free_team], gi[free_team]] = True
    seen = binary_dilation(seen, iterations=2) & reach
    coverage = seen.sum() / max(1, reach.sum())
    score = coverage * (0.5 * precision_occ + 0.5 * precision_free)
    return dict(score=float(score), coverage=float(coverage), precision_occupied=float(precision_occ),
                precision_free=float(precision_free), cells=int(len(gi)))


def evaluate_semantics(objects, ref, map_to_world, tol=2.0):
    """objects: list of dicts(object_class, color, label, x, y, status). Returns score 0..1 and details."""
    lms = [lm for lm in ref.sc['landmarks'] if lm.get('unique')]
    surv = list(ref.survivors.values())
    hits_lm, hits_sv, false = 0, 0, 0
    used = set()
    for o in objects:
        wx, wy = map_to_world(o['x'], o['y'])
        if o['object_class'] == 'survivor':
            m = [s for s in surv if math.hypot(s['x'] - wx, s['y'] - wy) <= tol and s['id'] not in used]
        else:
            m = [lm for lm in lms if lm['cls'] == o['object_class'] and lm['id'] not in used and
                 (not lm['color'] or not o.get('color') or lm['color'] == o['color']) and
                 math.hypot(lm['x'] - wx, lm['y'] - wy) <= tol + 0.5 * max(lm['size'][:2])]
        if m:
            used.add(m[0]['id'])
            if o['object_class'] == 'survivor':
                hits_sv += 1
            else:
                hits_lm += 1
        else:
            false += 1
    recall_lm = hits_lm / max(1, len(lms))
    recall_sv = hits_sv / max(1, len(surv))
    precision = (hits_lm + hits_sv) / max(1, hits_lm + hits_sv + false)
    return dict(score=float((0.5 * recall_lm + 0.5 * recall_sv) * (0.5 + 0.5 * precision)),
                landmarks_found=hits_lm, landmarks_total=len(lms), survivors_mapped=hits_sv,
                survivors_total=len(surv), false_objects=false)


def localization_error(samples):
    """samples: (t, (wx, wy) gt world, (tx, ty) team pose converted to world). Returns RMSE / max in m."""
    if not samples:
        return dict(rmse=-1.0, max=-1.0, n=0)
    e = np.array([math.hypot(a[0] - b[0], a[1] - b[1]) for _, a, b in samples])
    return dict(rmse=float(np.sqrt((e ** 2).mean())), max=float(e.max()), n=int(len(e)))
