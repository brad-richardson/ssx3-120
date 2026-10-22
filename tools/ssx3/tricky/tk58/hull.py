# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK58: TK33b's incremental convex hull with exact orientation tests on the 0.001 input grid that TK33b
already rounds to (integer determinants, so the visible set is exact and the horizon is one loop). The
result is verified (closed 2-manifold, Euler 2, F <= 2V-4, every input point behind every face plane
within tolerance). Output convention identical to make_scenery33._mesh."""
import math
from make_scenery33 import _sub,_cross,_dot,_mesh

def convex_hull_fixed(points):
    P = sorted({(round(p[0], 3), round(p[1], 3), round(p[2], 3)) for p in points})
    if len(P) < 4: return None
    Q = [tuple(int(round(c * 1000)) for c in p) for p in P]  # exact: inputs are already on a 0.001 grid
    def orient(a, b, c, d):  # 6 x signed volume of (a,b,c,d); > 0: d is in front of face (a,b,c)
        A, B, C, D = Q[a], Q[b], Q[c], Q[d]
        u = (B[0]-A[0], B[1]-A[1], B[2]-A[2]); v = (C[0]-A[0], C[1]-A[1], C[2]-A[2]); w = (D[0]-A[0], D[1]-A[1], D[2]-A[2])
        return u[0]*(v[1]*w[2]-v[2]*w[1]) - u[1]*(v[0]*w[2]-v[2]*w[0]) + u[2]*(v[0]*w[1]-v[1]*w[0])
    i0 = 0; i1 = max(range(len(P)), key=lambda i: _dot(_sub(P[i], P[i0]), _sub(P[i], P[i0])))
    d01 = _sub(P[i1], P[i0])
    i2 = max(range(len(P)), key=lambda i: _dot(c := _cross(d01, _sub(P[i], P[i0])), c))
    i3 = max(range(len(P)), key=lambda i: abs(orient(i0, i1, i2, i)))
    if orient(i0, i1, i2, i3) == 0: return None
    faces = set(); edge_face = {}
    def put(f):
        faces.add(f)
        for e in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])): edge_face[e] = f
    def drop(f):
        faces.discard(f)
        for e in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            if edge_face.get(e) == f: del edge_face[e]
    tet = (i0, i1, i2, i3)
    for a, b, c, x in ((i0, i1, i2, i3), (i0, i1, i3, i2), (i0, i2, i3, i1), (i1, i2, i3, i0)):
        put((a, b, c) if orient(a, b, c, x) < 0 else (a, c, b))  # outward: the opposite vertex is behind
    for i in sorted(set(range(len(P))) - set(tet)):
        vis = {f for f in faces if orient(*f, i) > 0}
        if not vis: continue
        horizon = [e for f in vis for e in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])) if edge_face.get((e[1], e[0])) not in vis]
        for f in vis: drop(f)
        for a, b in horizon: put((a, b, i))
    mesh = _mesh(P, list(faces))
    return mesh if verify(mesh, P)['ok'] else None

def verify(mesh, points):
    V = [v[:3] for v in mesh['vertices']]; T = mesh['triangles']; nV, nF = len(V), len(T)
    E = {}
    for a, b, c in T:
        for e in ((a, b), (b, c), (c, a)): E[e] = E.get(e, 0) + 1
    manifold = all(k == 1 and (e[1], e[0]) in E for e, k in E.items())
    ext = max(max(p[k] for p in points) - min(p[k] for p in points) for k in range(3)); tol = max(ext, 1.0) * 1e-4
    worst = 0.0
    for (a, b, c), nn in zip(T, mesh['normals']):
        d = _dot(nn[:3], V[a]); worst = max(worst, max(_dot(nn[:3], q) - d for q in points))
    euler = nV - len(E) // 2 + nF
    return dict(ok=manifold and nF <= 2 * nV - 4 and worst <= tol and euler == 2, verts=nV, faces=nF, manifold=manifold, euler=euler, worst_outside=worst, tol=tol)
