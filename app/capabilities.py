"""Public, non-sensitive capability discovery for MooBee clients.

Do not expose subscriber credentials, source URLs, admin hostnames, internal
network addresses, or feature flags for code not yet merged into main.
"""
from fastapi import APIRouter

router = APIRouter()

@router.get("/.well-known/moobee-capabilities")
def capabilities():
    return {
        "schema_version": 1,
        "service": "moobee-iptv",
        "streaming": {
            "xtream_player_api": "/player_api.php",
            "m3u_playlist": "/get.php",
            "xmltv_endpoint": "/xmltv.php",
            "supported_formats": ["hls"],
            "mpegts": False,
            "external_hls_ingest": False,
            "transcoding": False,
        },
        "features": {
            "live_tv": True,
            "vod": False,
            "series": False,
            "xmltv_program_data": False,
            "account_authentication": "xtream_credentials",
        },
        "administration": {
            "availability": "private_network_only",
            "discovery_url": None,
        },
    }
