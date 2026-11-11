import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media import store


class MediaStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'incoming.webp'
        self.source.write_bytes(b'RIFF-test-image-bytes')
        self.patcher = patch.object(store, '_root', return_value=self.root)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_save_file_persists_metadata_and_resolves_path(self):
        item = store.save_file(self.source, kind='sticker', mime='image/webp', note='wa sticker')
        self.assertTrue(item['id'].startswith('m_'))
        self.assertEqual(item['kind'], 'sticker')
        resolved = store.resolve_path(item['id'])
        self.assertTrue(Path(resolved).is_file())
        self.assertEqual(Path(resolved).read_bytes(), self.source.read_bytes())
        self.assertEqual(store.get_asset(item['id'])['note'], 'wa sticker')

    def test_duplicate_content_reuses_existing_asset(self):
        first = store.save_file(self.source, kind='image', mime='image/webp')
        other = self.root / 'copy.webp'
        other.write_bytes(self.source.read_bytes())
        second = store.save_file(other, kind='image', mime='image/webp')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(store.list_assets()), 1)

    def test_missing_asset_file_is_repaired_on_resave(self):
        first = store.save_file(self.source, kind='image', mime='image/webp')
        Path(first['path']).unlink()
        repaired = store.save_file(self.source, kind='image', mime='image/webp')
        self.assertEqual(first['id'], repaired['id'])
        self.assertTrue(Path(repaired['path']).is_file())

    def test_vision_can_resolve_saved_asset_to_a_data_url(self):
        from tools.builtin.vision import _resolve_source
        saved = store.save_file(self.source, kind='sticker', mime='image/webp')
        data_url = _resolve_source({'asset': saved['id']})
        self.assertTrue(data_url.startswith('data:image/webp;base64,'))

    def test_list_filters_by_kind_and_query(self):
        store.save_file(self.source, kind='sticker', mime='image/webp', note='cute cat')
        self.assertEqual(len(store.list_assets(kind='sticker')), 1)
        self.assertEqual(len(store.list_assets(kind='video')), 0)
        self.assertEqual(len(store.list_assets(query='cat')), 1)

    def test_assets_are_isolated_by_active_tenant(self):
        from unittest.mock import patch
        tenant_a = {'db': str(self.root / 'a' / 'kwee.db'), 'media': str(self.root / 'a' / 'media')}
        tenant_b = {'db': str(self.root / 'b' / 'kwee.db'), 'media': str(self.root / 'b' / 'media')}
        with patch.object(store, '_tenant_paths', return_value=tenant_a):
            saved = store.save_file(self.source, kind='image', mime='image/webp')
            self.assertTrue(Path(store.resolve_path(saved['id'])).is_file())
        with patch.object(store, '_tenant_paths', return_value=tenant_b):
            self.assertEqual(store.resolve_path(saved['id']), '')
            self.assertEqual(store.list_assets(), [])


if __name__ == '__main__':
    unittest.main()
