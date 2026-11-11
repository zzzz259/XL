import json
import struct
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from test_preview_portrait_particle_color import _renderer, _transform, _tree
from app.features.preview.portrait_config.model import AssetRef, ParticleState, RendererState, ScenePart
from app.features.preview.portrait_config.particle_metadata import (
    enrich_particle,
    normalize_material,
    normalize_mesh,
    normalize_system,
)

MESH_PATH_ID = 7625927331971706890


def _mesh_object():
    vertices = [
        ((1.0, -1.0, 0.0), (0.0, 0.0)),
        ((1.0, 1.0, 0.0), (0.0, 1.0)),
        ((-1.0, 1.0, 0.0), (1.0, 1.0)),
        ((-1.0, -1.0, 0.0), (1.0, 0.0)),
    ]
    data = b''.join(
        struct.pack('<3f3f4f2f', *position, 0, 0, 1, -1, 0, 0, -1, *uv)
        for position, uv in vertices
    )
    channels = [
        SimpleNamespace(dimension=3, format=0, offset=0, stream=0),
        SimpleNamespace(dimension=3, format=0, offset=12, stream=0),
        SimpleNamespace(dimension=4, format=0, offset=24, stream=0),
        SimpleNamespace(dimension=0, format=0, offset=0, stream=0),
        SimpleNamespace(dimension=2, format=0, offset=40, stream=0),
    ]
    return SimpleNamespace(
        m_Name='Mesh_low_plane',
        m_IndexFormat=0,
        m_IsReadable=True,
        m_VertexData=SimpleNamespace(m_DataSize=data, m_VertexCount=4, m_Channels=channels),
        m_IndexBuffer=[0, 0, 1, 0, 2, 0, 0, 0, 2, 0, 3, 0],
        m_SubMeshes=[SimpleNamespace(
            firstByte=0, indexCount=6, topology=0, baseVertex=0,
            firstVertex=0, vertexCount=4,
            localAABB={'m_Center': {'x': 0, 'y': 0, 'z': 0}, 'm_Extent': {'x': 1, 'y': 1, 'z': 0}},
        )],
    )


def _additive_material():
    return {
        'm_ValidKeywords': [],
        'm_SavedProperties': {
            'm_Floats': {'_SrcBlend': 1, '_DstBlend': 0, '_DeadStrength': 0.01, '_AlphaRatio': 1},
            'm_Colors': {
                '_TintColor': {'r': 0.5, 'g': 0.5, 'b': 0.5, 'a': 0.5},
                '_DarkColorExt': {'r': 1, 'g': 1, 'b': 1, 'a': 1},
            },
            'm_TexEnvs': {'_MainTex': {'m_Scale': {'x': 1, 'y': 1}, 'm_Offset': {'x': 0, 'y': 0}}},
        },
    }


class FakeIndex:
    def __init__(self):
        self.material = AssetRef('material', 1)
        self.shader = AssetRef('shader', -2521961875066890481)
        self.texture = AssetRef('texture', 3)
        self.mesh = AssetRef('mesh', MESH_PATH_ID)
        self.renderer = AssetRef('renderer', 8)
        self.mesh_object = _mesh_object()
        self.trees = {
            self.material: _additive_material(),
            self.shader: {'m_ParsedForm': {'m_Name': 'SoulGames/Effects/Additive'}},
            self.renderer: {'m_Mesh': {'m_FileID': 1, 'm_PathID': MESH_PATH_ID}},
            self.mesh: {
                'm_Name': 'Mesh_low_plane', 'm_IndexFormat': 0, 'm_IsReadable': True,
                'm_SubMeshes': self.mesh_object.m_SubMeshes,
            },
        }

    def object_tree(self, reference):
        return self.trees[reference]

    def resolve_object_pointer(self, source, pointer, expected_type=None):
        return {('Shader',): self.shader, ('Mesh',): self.mesh, ('Texture2D',): self.texture}[(expected_type,)]

    def bundle_path_for(self, bundle_hash):
        return bundle_hash

    @contextmanager
    def _environment(self, _path):
        yield SimpleNamespace(objects=[SimpleNamespace(path_id=MESH_PATH_ID, read=lambda: self.mesh_object)])


class ParticleMeshTests(unittest.TestCase):
    def test_actual_mesh_quad_decodes_positions_uv_and_indices(self):
        normalized = normalize_mesh(_mesh_object(), MESH_PATH_ID)
        self.assertEqual(normalized['kind'], 'mesh_quad_v1')
        self.assertEqual(normalized['positions'], [[1, -1, 0], [1, 1, 0], [-1, 1, 0], [-1, -1, 0]])
        self.assertEqual(normalized['uvs'], [[0, 0], [0, 1], [1, 1], [1, 0]])
        self.assertEqual(normalized['indices'], [0, 1, 2, 0, 2, 3])
        with self.assertRaises(ValueError):
            normalize_mesh(_mesh_object(), 123)

    def test_mesh_config_rejects_missing_or_changed_geometry(self):
        renderer = _renderer()
        renderer['m_RenderMode'] = 4
        system = normalize_system(_tree(), renderer, 7, [_transform()])
        mesh = normalize_mesh(_mesh_object(), MESH_PATH_ID)
        material = normalize_material(_additive_material(), 'SoulGames/Effects/Additive', -2521961875066890481)
        config = {'version': 1, 'system': system, 'material': material, 'mesh': mesh}
        from app.features.preview.portrait_config.particle_metadata import validate_config
        validate_config(json.dumps(config), 'additive_v1')
        mesh['uvs'][0][0] = 1
        with self.assertRaisesRegex(ValueError, 'Mesh'):
            validate_config(json.dumps(config), 'additive_v1')
        del config['mesh']
        with self.assertRaisesRegex(ValueError, 'Mesh'):
            validate_config(json.dumps(config), 'additive_v1')

    def test_enrich_particle_resolves_renderer_mesh(self):
        renderer_tree = _renderer(alignment=2)
        renderer_tree['m_RenderMode'] = 4
        config = {'version': 1, 'system': normalize_system(_tree(), renderer_tree, 7, [_transform()])}
        index = FakeIndex()
        system = AssetRef('system', 7)
        renderer = AssetRef('renderer', 8)
        part = ScenePart(
            part_id='ParticleSystemRenderer:bundle:8',
            object_ref=renderer,
            game_object_name='Particle System',
            renderer=RendererState(kind='ParticleSystemRenderer', enabled=True, material=index.material),
            particle=ParticleState(system=system, config_json=json.dumps(config), profile='unsupported'),
        )
        enriched, diagnostics = enrich_particle(part, index)
        self.assertEqual(diagnostics, ())
        self.assertEqual(enriched.particle.profile, 'additive_v1')
        self.assertEqual(enriched.renderer.mesh, index.mesh)
        mesh = json.loads(enriched.particle.config_json)['mesh']
        self.assertEqual(mesh['kind'], 'mesh_quad_v1')
        self.assertEqual(len(mesh['positions']), 4)
        self.assertEqual(len(mesh['indices']), 6)


if __name__ == '__main__':
    unittest.main()
