"""Unity TRS projection for per-part Spine rendering in a common XY frame."""
from __future__ import annotations

import math

REFERENCE_SCALE = 0.01


def world_matrix(chain):
    world = ((1., 0., 0., 0.), (0., 1., 0., 0.), (0., 0., 1., 0.), (0., 0., 0., 1.))
    for node in chain:
        p, q, s = tuple(node.local_position), tuple(node.local_rotation), tuple(node.local_scale)
        if len(p) != 3 or len(q) != 4 or len(s) != 3:
            raise ValueError('Unity transform dimensions must be position3, quaternion4, scale3')
        if not all(math.isfinite(float(x)) for x in (*p, *q, *s)):
            raise ValueError('Unity transforms must be finite')
        norm = math.sqrt(sum(float(x) ** 2 for x in q))
        if norm < 1e-12:
            raise ValueError('Unity quaternion has zero length')
        x, y, z, w = (float(v) / norm for v in q)
        sx, sy, sz = map(float, s)
        local = (
            ((1-2*(y*y+z*z))*sx, 2*(x*y-z*w)*sy, 2*(x*z+y*w)*sz, float(p[0])),
            (2*(x*y+z*w)*sx, (1-2*(x*x+z*z))*sy, 2*(y*z-x*w)*sz, float(p[1])),
            (2*(x*z-y*w)*sx, 2*(y*z+x*w)*sy, (1-2*(x*x+y*y))*sz, float(p[2])),
            (0., 0., 0., 1.),
        )
        world = tuple(tuple(sum(world[i][k] * local[k][j] for k in range(4))
                            for j in range(4)) for i in range(4))
    return world


def scene_origin(parts):
    """Remove a shared root translation; preserve every rotation and reflection."""
    active = tuple(p for p in parts if p.renderer is None or p.renderer.enabled)
    if not active or any(not p.transform_chain for p in active):
        return (0., 0.)
    first = active[0].transform_chain[0]
    if any(p.transform_chain[0] != first for p in active):
        return (0., 0.)
    matrix = world_matrix((first,))
    return matrix[0][3], matrix[1][3]


def part_affine(chain, skeleton_scale=REFERENCE_SCALE, *, origin=(0., 0.)):
    scale = float(skeleton_scale)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('SkeletonDataAsset scale must be finite and positive')
    m = world_matrix(chain)
    ratio = scale / REFERENCE_SCALE
    result = (m[0][0]*ratio, m[0][1]*ratio, m[1][0]*ratio, m[1][1]*ratio,
              (m[0][3]-origin[0])/REFERENCE_SCALE, (m[1][3]-origin[1])/REFERENCE_SCALE)
    if not all(math.isfinite(x) for x in result):
        raise ValueError('Projected transform must be finite')
    if abs(result[0]*result[3] - result[1]*result[2]) < 1e-12:
        raise ValueError('Unity XY projection is degenerate')
    return result
