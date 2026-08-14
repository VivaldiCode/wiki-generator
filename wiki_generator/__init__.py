"""wiki-generator: gera uma wiki completa de um repositorio via Claude Code headless."""

__version__ = "0.1.0"

# Bump quando a estrutura da wiki ou os prompts mudarem de forma incompativel.
# Faz parte do fingerprint de cache: mudar isto invalida todas as paginas.
STRUCTURE_VERSION = "1"

__all__ = ["__version__", "STRUCTURE_VERSION"]
