"""
Version management for py-aufsichtsplan
"""

__version__ = "1.0.0"
__build_date__ = "2024-01-15"

def get_version_info():
    """Returns version information as dictionary"""
    return {
        "version": __version__,
        "build_date": __build_date__
    }
