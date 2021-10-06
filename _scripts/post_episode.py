"""Update the RSS feed."""

import datetime as dt
import os
import subprocess
import sys
import uuid
import yaml
from copy import deepcopy
from datetime import datetime
from lxml import etree
from lxml.etree import ElementTree as ET
from pkg_resources import resource_filename
from yaml.scanner import ScannerError

import click
import feedparser
import librosa

REQUIRED_ITEMS = ['title', 'number', 'description', 'keywords']
GCS_DIRECTORY = 'gs://song-of-urania/episodes'
GCS_URL = 'https://storage.googleapis.com/song-of-urania/episodes'
WEBSITE = 'https://songofurania.com'

# TODO:
# * [X] Create a YAML template for new episode metadata.
# * [X] Launch an editor for the user to input the metadata.
# * [X] Set the pubDate based on the current time.
# * [X] Check that an mp3 file has been passed in via a CLI argument.
# * [X] Get the duration of the mp3 file.
# * [X] Set the item data.
# * [X] Create the new RSS XML file.  (Sanitize input like ampersands.)
# * [X] Upload the mp3 file to GCS.
# * [X] Make a custom webpage for the new episode.
# * [ ] Handle long iTunes subtitles.
# * [ ] Check HTML character sanitization.
# * [ ] Add more unit tests.
# * [ ] Handle publication on a schedule.


def validate_new_episode_metadata(metadata):
    """Validate that user-provided metadata has all needed information."""
    for elem in REQUIRED_ITEMS:
        if elem not in metadata:
            raise ValueError(f'Metadata must include {elem}.')

    if not isinstance(metadata['number'], int):
        raise ValueError(
            f'Episode number must be integer, but got type '
            f'{type(metadata["number"])}.'
        )
    if not isinstance(metadata['keywords'], list):
        raise ValueError(
            f'Keywords must be list, but got type '
            f'{type(metadata["keywords"])}.'
        )


def gen_yaml_template():
    """Generate the YAML template that is used to input user metadata."""

    yaml_template = []
    for key in REQUIRED_ITEMS:
        yaml_template.append(f'{key}: ')
        if key == 'description':
            yaml_template[-1] += '|\n  '

    return '\n'.join(yaml_template)


def get_new_episode_metadata():
    new_episode_metadata = None
    raw_new_episode_metadata = gen_yaml_template()
    while new_episode_metadata is None:
        raw_new_episode_metadata = click.edit(
            raw_new_episode_metadata, extension='.yaml'
        )

        try:
            new_episode_metadata = yaml.safe_load(raw_new_episode_metadata)
        except ScannerError:
            if not click.confirm('Could not parse YAML. Try again?'):
                sys.exit(1)

        while True:
            try:
                validate_new_episode_metadata(new_episode_metadata)
            except ValueError:
                if not click.confirm('Missing data. Try again?'):
                    sys.exit(1)
                else:
                    continue
            break

    description = new_episode_metadata['description']
    description = description.replace('\n', ' ')
    description = description.strip()
    new_episode_metadata['description'] = description

    return new_episode_metadata


def validate_rss(rss_filename):
    rss = feedparser.parse(rss_filename)
    if rss.bozo:
        raise rss.bozo_exception


def get_latest_episode_and_index(rss):
    for i, elem in enumerate(rss[0]):
        if elem.tag == 'item':
            return i, elem


def get_formatted_pubdate(time=None):
    """Return a properly formatted publication time.

    If no time is provided, this will provide the current system time.

    """
    if time is None:
        time = datetime.now(dt.timezone.utc)

    return time.strftime('%a, %d %b %Y %H:%M:%S %z')


def get_formatted_duration(mp3_filename):
    """Return the duration of the episode formatted as HH:MM."""

    duration = librosa.get_duration(filename=mp3_filename)
    return f'{int(duration // 60):02}:{int(round(duration % 60)):02}'


def get_namespaces(filename):
    namespaces = etree.iterparse(filename, events=['start-ns'])
    return dict([n for _, n in namespaces])


def abbreviate_str(s, n_chars=255):
    """Abbreviate the given string to be at most `n_chars` characters long."""
    if len(s) <= n_chars:
        return s

    words = s.split()
    abbreviated_words = []
    total = 0
    for i, word in enumerate(words):
        if total + len(word) + 1 > n_chars:
            last_word = word
            while total + len(last_word) + 1 > n_chars:
                if len(abbreviated_words) > 1:
                    last_word = abbreviated_words.pop()
                    abbreviated_str = ''.join(abbreviated_words) + '...'
                else:
                    abbreviated_str = abbreviated_words[0][:n_chars-3] + '...'
                    break
        else:
            abbreviated_words.append(word)
            total += len(word)
            if i > 0:
                total += 1  # Include the space before the word.

    return abbreviated_str


def update_new_episode_node(node, metadata, namespaces, mp3_filename):
    itunes_ns = namespaces['itunes']
    node.find('title').text = (
        f'Episode {metadata["number"]}: ' + metadata['title']
    )
    node.find(f'{{{itunes_ns}}}title').text = metadata['title']
    node.find('pubDate').text = get_formatted_pubdate()
    node.find('guid').text = etree.CDATA(str(uuid.uuid4()))

    node.find('link').text = etree.CDATA(
        os.path.join(WEBSITE, 'episodes', f'{metadata["number"]:03}')
    )
    node.find('description').text = etree.CDATA(
        '<p>' + metadata['description'] + '</p>'
    )
    node.find(f'{{{namespaces["content"]}}}encoded').text = etree.CDATA(
        '<p>' + metadata['description'] + '</p>'
    )
    node.find('enclosure').attrib['length'] = str(
        os.path.getsize(mp3_filename)
    )
    node.find('enclosure').attrib['url'] = os.path.join(
        GCS_URL, f'episode-{metadata["number"]:03}.mp3'
    )
    node.find(f'{{{itunes_ns}}}duration').text = get_formatted_duration(
        mp3_filename
    )
    node.find(f'{{{itunes_ns}}}keywords').text = ','.join(metadata['keywords'])
    node.find(f'{{{itunes_ns}}}subtitle').text = etree.CDATA(
        metadata['description']
    )
    node.find(f'{{{itunes_ns}}}summary').text = metadata['description']
    node.find(f'{{{itunes_ns}}}episode').text = str(metadata['number'])

    return node


def update_rss(rss_filename, mp3_filename):
    parser = etree.XMLParser(strip_cdata=False)
    tree = etree.parse(rss_filename, parser)
    rss = tree.getroot()
    namespaces = get_namespaces(rss_filename)

    # Necessary to keep the prefixes of the namespaces the same rather than
    # reverting to Python's default of 'ns0', 'ns1', etc.
    #for prefix, uri in namespaces.items():
    #    ET.register_namespace(prefix, uri)
    validate_rss(rss_filename)

    new_episode_metadata = get_new_episode_metadata()

    latest_episode_index, latest_episode = get_latest_episode_and_index(rss)
    new_episode = deepcopy(latest_episode)

    # Check to see if the new episode number is one greater than the last
    # episode number.
    itunes_ns = namespaces['itunes']
    last_episode_number = latest_episode.find(f'{{{itunes_ns}}}episode').text
    if new_episode_metadata['number'] != int(last_episode_number) + 1:
        confirmation = click.confirm(
            f'Last episode number was {last_episode_number} but trying to '
            f'create episode {new_episode_metadata["number"]}. Continue?'
        )
        if not confirmation:
            sys.exit(1)

    new_episode = update_new_episode_node(
        new_episode, new_episode_metadata, namespaces, mp3_filename
    )

    rss[0].insert(latest_episode_index, new_episode)
    rss[0].find('pubDate').text = get_formatted_pubdate()
    rss[0].find('lastBuildDate').text = get_formatted_pubdate()

    tree.write(rss_filename)
    validate_rss(rss_filename)

    return new_episode_metadata


def upload_to_gcs(mp3_filename, metadata):
    """Upload the MP3 file to GCS."""
    basename = os.path.basename(mp3_filename)
    if basename != f'episode-{metadata["number"]:03}.mp3':
        raise ValueError(
            f'Expected filename "episode-{metadata["number"]:03}.mp3" but got '
            f'{mp3_filename}.'
        )
    subprocess.check_call(['gsutil', 'cp', mp3_filename, GCS_DIRECTORY])


def update_webpage(metadata):
    """Create a webpage for the new episode."""
    date_str = dt.date.today().strftime('%Y-%m-%d')
    page_name = date_str + f'-{metadata["number"]:03}.md'
    scripts_path = resource_filename(__name__, '')
    root_path = os.path.dirname(scripts_path)
    page_path = os.path.join(root_path, '_posts', page_name)

    if os.path.isfile(page_path):
        raise RuntimeError(f'File {page_path} already exists!')

    page_metadata = deepcopy(metadata)
    page_metadata['layout'] = 'episode'
    page_metadata['categories'] = 'episode'
    page_metadata['date'] = date_str
    with open(page_path, 'w') as fp:
        fp.write('---\n')
        yaml.dump(page_metadata, fp)
        fp.write('---')


@click.command()
@click.option('--rss_filename', default='rss')
@click.argument('mp3_filename')
def post_episode(rss_filename, mp3_filename):
    metadata = update_rss(rss_filename, mp3_filename)
    upload_to_gcs(mp3_filename, metadata)
    update_webpage(metadata)


if __name__ == '__main__':
    post_episode()
