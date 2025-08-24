"""
Version management for py-aufsichtsplan
"""

__version__ = "0.2.6-beta"
__build_date__ = "2025-08-24"

def get_version_info():
    """Returns version information as dictionary"""
    return {
        "version": __version__,
        "build_date": __build_date__
    }
