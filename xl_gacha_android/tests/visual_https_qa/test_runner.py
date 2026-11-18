import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree


class RunnerTest(unittest.TestCase):
    def test_tls_trust_is_limited_to_copied_qa_package(self):
        from run import ANDROID, configure_qa

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            android = source / 'xl_gacha_android'
            data = source / 'xl_gacha_web' / 'data'
            android.mkdir(parents=True)
            data.mkdir(parents=True)
            (android / 'AndroidManifest.xml').write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
                '<application android:usesCleartextTraffic="false" /></manifest>', encoding='utf-8',
            )
            ca = root / 'ca.pem'
            ca.write_bytes(b'qa-ca')
            output = root / 'out'
            configure_qa(source, output, ca)
            app = ElementTree.parse(android / 'AndroidManifest.xml').getroot().find('application')
            self.assertEqual(app.attrib[ANDROID + 'networkSecurityConfig'], '@xml/visual_qa_network')
            self.assertEqual(app.attrib[ANDROID + 'usesCleartextTraffic'], 'false')
            network = ElementTree.parse(output / 'res/xml/visual_qa_network.xml').getroot()
            domain = network.find('domain-config')
            self.assertEqual(domain.attrib['cleartextTrafficPermitted'], 'false')
            self.assertEqual(domain.find('domain').text, 'localhost')
            self.assertEqual(domain.find('trust-anchors/certificates').attrib, {'src': '@raw/visual_qa_ca'})
            self.assertEqual(output.joinpath('res/raw/visual_qa_ca.pem').read_bytes(), b'qa-ca')
            self.assertEqual(data.joinpath('update-source.json').read_text(), '{"url":""}\n')

    def test_stage_files_distinguish_registered_cases(self):
        from run import fixture_request

        stages = {'cold': 'V3', 'old': 'V1'}
        versions = {'V1': '11', 'V2': '12', 'V3': '13'}
        self.assertEqual(fixture_request('/old/current.json', stages, versions), ('old', 'V1', 'current.json'))
        self.assertEqual(fixture_request('/cold/catalog-12.json', stages, versions), ('cold', 'V2', 'catalog-12.json'))
        self.assertIsNone(fixture_request('/old/../cold/current.json', stages, versions))
        self.assertIsNone(fixture_request('/old/current.json?look', stages, versions))
        self.assertIsNone(fixture_request('/unknown/current.json', stages, versions))


if __name__ == '__main__':
    unittest.main()
