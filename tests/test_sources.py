from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import pytest
from source_management import parse_local_m3u

def test_local_import_parser():
    payload='#EXTM3U\n#EXTINF:-1 group-title="Personal" tvg-name="Test",Test\nmoobee-local://channel1\n'
    result=parse_local_m3u(payload)
    assert result[0]['folder']=='channel1'
    assert result[0]['name']=='Test'

@pytest.mark.parametrize('entry', [
    'https://example.com/a.m3u8',
    'http://127.0.0.1/internal',
    'moobee-local://../secrets',
    'moobee-local://folder/sub'
])
def test_reject_remote_and_unsafe_references(entry):
    payload='#EXTM3U\n#EXTINF:-1,Test\n'+entry+'\n'
    with pytest.raises(ValueError):
        parse_local_m3u(payload)
