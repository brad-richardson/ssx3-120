"""Bounded Tricky GC collision reader and SSX 3 GC triangle-mesh encoder.

Target type 1 stores uint8 triangle indices, one bbox per ten triangles,
16-byte vertices/normals, and sequential meshes. Source uint32 indices require
partitioning at 256 vertices. Mesh data stays local to the instance transform.
"""
import math
import struct


def collision_instance_transform(data, *, normalize=False):
    """Validate SSX 3's rigid basis / separate uniform scale representation.

    GXBE69 801DDF98 calls a transpose-based rigid inverse (801C651C), then
    scales the query by 1 / instance+124 at 801DDFA4. Baking scale into the
    basis preserves the render transform but gives the wrong collision inverse.
    Normalization preserves the forward transform within float32 rounding.
    Nonuniform scale and shear require a different geometry conversion.
    """
    if len(data) != 160:
        raise ValueError('Expected a 160-byte static instance')
    matrix=list(struct.unpack_from('>16f',data,8))
    scalar=struct.unpack_from('>f',data,124)[0]
    if (not all(math.isfinite(v) for v in matrix+[scalar]) or scalar <= 0 or
            matrix[3:12:4] != [0.,0.,0.] or matrix[15] != 1.):
        raise ValueError('Invalid collision instance transform')
    axes=[matrix[4*i:4*i+3] for i in range(3)]
    lengths=[math.sqrt(sum(v*v for v in axis)) for axis in axes]
    scale=sum(lengths)/3
    if (scale <= 0 or any(abs(length/scale-1)>1e-5 for length in lengths) or
            any(abs(sum(axes[i][k]*axes[j][k] for k in range(3)))>scale*scale*1e-5
                for i in range(3) for j in range(i))):
        raise ValueError('Collision instance has nonuniform scale or shear')
    if not normalize:
        if abs(scale-1)>1e-5:
            raise ValueError('Collision instance basis must be orthonormal; use the separate scale field')
        return data
    for i in range(3):
        for j in range(3):matrix[4*i+j]/=scale
    result=bytearray(data)
    struct.pack_into('>16f',result,8,*matrix)
    struct.pack_into('>f',result,124,scalar*scale)
    collision_instance_transform(result)
    return bytes(result)


def span(data, at, size, low=0, high=None):
    high = len(data) if high is None else high
    if size < 0 or at < low or at+size > high or high > len(data):
        raise ValueError('Collision data outside its section')


def vectors(data, at, count, end, w):
    span(data, at, 16*count, high=end)
    result = [struct.unpack_from('>4f', data, at+16*i) for i in range(count)]
    if any(not all(math.isfinite(v) for v in p) or p[3] != w for p in result):
        raise ValueError('Unsupported/nonfinite collision vector')
    return result


def read_tricky_collision(data):
    if len(data) < 76 or struct.unpack_from('>I', data)[0] != 0x00021e00:
        raise ValueError('Expected a GameCube Tricky GSF')
    count, table = struct.unpack_from('>2I', data, 28)
    table_end = struct.unpack_from('>I', data, 48)[0]  # function headers follow pointers
    if not 76 <= table <= table_end <= len(data) or table+12*count != table_end:
        raise ValueError('Invalid source collision pointer table')
    result = []
    occupied = []
    for i in range(count):
        at, size, parts = struct.unpack_from('>3I', data, table+12*i)
        end = at+size
        span(data, at, size, low=table_end)
        if not 0 < parts <= 65535:
            raise ValueError('Invalid source collision mesh count')
        occupied.append((at,end))
        meshes = []
        for _ in range(parts):
            span(data, at, 12, high=end)
            faces, verts, padding = struct.unpack_from('>3I', data, at)
            if padding > 15:
                raise ValueError('Unsupported source vertex alignment')
            at += 12
            span(data, at, 12*faces+padding, high=end)
            indices = [struct.unpack_from('>3I', data, at+12*j) for j in range(faces)]
            if any(v >= verts for tri in indices for v in tri):
                raise ValueError('Collision triangle references absent vertex')
            at += 12*faces+padding
            positions = vectors(data, at, verts, end, 1.)
            at += 16*verts
            normals = vectors(data, at, faces, end, 0.)
            at += 16*faces
            meshes.append(dict(vertices=positions, triangles=indices, normals=normals))
        if at != end:
            raise ValueError('Unexpected source collision trailing bytes')
        result.append(meshes)
    for a,b in zip(sorted(set(occupied)), sorted(set(occupied))[1:]):
        if a[1]>b[0]:
            raise ValueError('Overlapping source collision data')
    return result


def partition(mesh):
    vertices, triangles, normals = mesh['vertices'], mesh['triangles'], mesh['normals']
    if len(triangles) != len(normals):
        raise ValueError('Expected one normal per collision triangle')
    result, mapping, points, faces, planes = [], {}, [], [], []
    for tri, normal in zip(triangles, normals):
        if len(tri)!=3 or any(not isinstance(i,int) or not 0 <= i < len(vertices) for i in tri):
            raise ValueError('Invalid collision triangle')
        if len(mapping)+len(set(tri)-mapping.keys()) > 256 or len(faces) == 65535:
            result.append(dict(vertices=points, triangles=faces, normals=planes))
            mapping, points, faces, planes = {}, [], [], []
        for i in tri:
            if i not in mapping:
                mapping[i] = len(points); points.append(vertices[i])
        faces.append(tuple(mapping[i] for i in tri)); planes.append(normal)
    if faces:
        result.append(dict(vertices=points, triangles=faces, normals=planes))
    return result


def encode_collision(source, *, geometry_scale=1):
    if geometry_scale not in (1,4):
        raise ValueError('Expected verified GC instance geometry compensation (1 or 4)')
    parts = [part for mesh in source for part in partition(mesh)]
    if not 0 < len(parts) <= 65535:
        raise ValueError('Expected a nonempty collision mesh')
    data = bytearray(16)
    for mesh in parts:
        at = len(data)
        vertices = [tuple(v/geometry_scale for v in p[:3])+(1.,) for p in mesh['vertices']]
        vertices = [struct.unpack('>4f',struct.pack('>4f',*p)) for p in vertices]
        # Tricky normals follow (b-a)x(c-a); SSX 3 collision normals use the
        # opposite winding (all 8,959 audited stock faces). Preserve the
        # authored outward normal and reverse indices for target edge tests.
        triangles = [(a,c,b) for a,b,c in mesh['triangles']]
        normals = mesh['normals']
        if any(not all(math.isfinite(v) for v in p) for p in vertices+normals):
            raise ValueError('Nonfinite collision geometry')
        if any(len(p)!=4 or p[3]!=0 for p in normals):
            raise ValueError('Unsupported collision normal')
        data += bytes(20)
        data += bytes(i for tri in triangles for i in tri)
        data += bytes(-len(data)%4)
        bbox = len(data)-at
        for start in range(0,len(triangles),10):
            points=[vertices[i] for tri in triangles[start:start+10] for i in tri]
            data += struct.pack('>6f', *[min(p[k] for p in points) for k in range(3)],
                                      *[max(p[k] for p in points) for k in range(3)])
        data += bytes(-len(data)%16)
        verts = len(data)-at
        for p in vertices: data += struct.pack('>4f',*p)
        norms = len(data)-at
        for p in normals: data += struct.pack('>4f',*p)
        struct.pack_into('>2H4I',data,at,len(triangles),len(vertices),20,bbox,verts,norms)
    tail = len(data)
    data += bytes.fromhex('efbeaddeefbeadde0000000000000000')
    data += bytes.fromhex('efbeadde')*len(parts)
    struct.pack_into('>2H3I',data,0,1,len(parts),16,tail,tail+16)
    return bytes(data)


def read_collision(data, *, bounds_tolerance=0.):
    if not math.isfinite(bounds_tolerance) or bounds_tolerance < 0:
        raise ValueError('Invalid collision bounds tolerance')
    span(data,0,16)
    kind,count,at,tail,refs = struct.unpack_from('>2H3I',data)
    if kind != 1 or not count or at!=16 or refs!=tail+16 or refs+4*count!=len(data):
        raise ValueError('Unsupported target triangle collision header')
    result=[]
    for _ in range(count):
        span(data,at,20,high=tail)
        faces,verts,indices,bbox,pos,norm = struct.unpack_from('>2H4I',data,at)
        boxes=(faces+9)//10
        if (verts>256 or indices!=20 or bbox < indices+3*faces or bbox%4 or
                pos < bbox+24*boxes or (at+pos)%16 or norm!=pos+16*verts):
            raise ValueError('Invalid target collision offsets')
        span(data,at, norm+16*faces,high=tail)
        triangles=[tuple(data[at+indices+3*i:at+indices+3*i+3]) for i in range(faces)]
        if any(i>=verts for tri in triangles for i in tri):
            raise ValueError('Target collision triangle references absent vertex')
        vertices=vectors(data,at+pos,verts,tail,1.)
        normals=vectors(data,at+norm,faces,tail,0.)
        for tri,normal in zip(triangles,normals):
            a,b,c=[vertices[i] for i in tri]
            u,v=[b[k]-a[k] for k in range(3)],[c[k]-a[k] for k in range(3)]
            cross=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            if sum(x*y for x,y in zip(cross,normal))>1e-6:
                raise ValueError('Collision triangle winding disagrees with SSX 3 normal convention')
        for j in range(boxes):
            bounds=struct.unpack_from('>6f',data,at+bbox+24*j)
            if any(not math.isfinite(v) for v in bounds):
                raise ValueError('Nonfinite collision bounds')
            if any(not bounds[k]-bounds_tolerance <= vertices[i][k] <= bounds[k+3]+bounds_tolerance
                   for tri in triangles[10*j:10*j+10] for i in tri for k in range(3)):
                raise ValueError('Collision bounds miss their triangle batch')
        result.append(dict(vertices=vertices,triangles=triangles,normals=normals))
        at+=norm+16*faces
    if at!=tail:
        raise ValueError('Unexpected target mesh trailing bytes')
    return result
