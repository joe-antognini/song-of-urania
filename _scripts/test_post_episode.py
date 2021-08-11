import datetime as dt
import os
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from datetime import datetime
from pkg_resources import resource_filename

import numpy as np
import soundfile as sf

import post_episode

TEST_RSS_FILENAME = resource_filename(__name__, 'test_rss.xml')


class ValidateNewMetadata(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            'title': 'Foo',
            'number': 1,
            'description': 'Bar',
            'keywords': ['baz', 'qux'],
        }

    def test_valid_metadata(self):
        post_episode.validate_new_episode_metadata(self.metadata)

    def test_no_title(self):
        del self.metadata['title']
        with self.assertRaises(ValueError):
            post_episode.validate_new_episode_metadata(self.metadata)

    def test_bad_number(self):
        self.metadata['number'] = 1.5
        with self.assertRaises(ValueError):
            post_episode.validate_new_episode_metadata(self.metadata)

    def test_bad_keywords(self):
        self.metadata['keywords'] = 'foo'
        with self.assertRaises(ValueError):
            post_episode.validate_new_episode_metadata(self.metadata)


class TestUpdateRSS(unittest.TestCase):
    def test_validate_rss(self):
        post_episode.validate_rss(TEST_RSS_FILENAME)

    @unittest.mock.patch(
        'post_episode.REQUIRED_ITEMS', ['title', 'description']
    )
    def test_gen_yaml_template(self):
        template = post_episode.gen_yaml_template()
        self.assertEqual(template, 'title: \ndescription: |\n  ')

    def test_get_latest_episode_and_idx(self):
        tree = ET.parse(TEST_RSS_FILENAME)
        rss = tree.getroot()
        idx, node = post_episode.get_latest_episode_and_index(rss)
        self.assertEqual(idx, 22)
        self.assertEqual(
            node.find('title').text, 'Episode 1: The Heavens & History'
        )

    def test_get_formatted_pubdate(self):
        date = datetime(2021, 7, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
        date_str = post_episode.get_formatted_pubdate(date)
        self.assertEqual(date_str, 'Fri, 02 Jul 2021 03:04:05 +0000')

    def test_get_formatted_duration(self):
        audio = np.random.uniform(-0.5, 0.5, size=1003200)
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'foo.wav')
            sf.write(filename, audio, samplerate=16000)
            duration_str = post_episode.get_formatted_duration(filename)

        self.assertEqual(duration_str, '01:03')

    def test_get_namespaces(self):
        namespaces = post_episode.get_namespaces(TEST_RSS_FILENAME)
        self.assertIn('itunes', namespaces)
        self.assertEqual(
            namespaces['itunes'], 'http://www.itunes.com/dtds/podcast-1.0.dtd'
        )
