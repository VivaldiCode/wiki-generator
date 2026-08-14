"""wiki-generator: build a complete wiki for a repository via headless Claude Code."""

__version__ = "0.1.0"

# Bump when the wiki structure or the prompts change incompatibly.
# Part of the cache fingerprint: changing it invalidates every page.
STRUCTURE_VERSION = "1"

__all__ = ["__version__", "STRUCTURE_VERSION"]
