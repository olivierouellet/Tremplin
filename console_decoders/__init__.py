import importlib.util
import os
import sys

from .base import ConsoleDecoder, SerialConfig
from .swiss_timing_ares21 import Ares21Decoder
from .cts_gen6 import CTSGen6Decoder
from .cts_gen7 import CTSGen7Decoder
from .omnisport_2000 import Omnisport2000Decoder
from .quantum import QuantumDecoder

# Each entry: (settings key, human-readable label, decoder class key)
# The settings key is what gets stored in settings.json as console_type.
# Multiple console models can share the same decoder when the protocol is identical.
CONSOLE_OPTIONS: list[tuple[str, str, str]] = [
    ('cts_gen5',        'System 5 (Colorado Timing System)',         'cts_gen6'),
    ('cts_gen6',        'System 6 (Colorado Timing System)',         'cts_gen6'),
    ('cts_gen7_legacy', 'Gen7 Legacy (Colorado Timing System)',      'cts_gen6'),
    ('cts_gen7',        'Gen7 Serial (Colorado Timing System)',      'cts_gen7'),
    ('dak_2000',        'Omnisport 2000 (Daktronics)',               'dak_2000'),
    ('omega_ares21',    'Ares 21 (Swiss Timing Omega)',              'omega_ares21'),
    ('omega_quantum',   'Quantum (Swiss Timing Omega)',              'omega_quantum'),
]

DECODERS: dict[str, type[ConsoleDecoder]] = {
    'cts_gen6':        CTSGen6Decoder,
    'cts_gen7':        CTSGen7Decoder,
    'dak_2000':        Omnisport2000Decoder,
    'omega_ares21':    Ares21Decoder,
    'omega_quantum':   QuantumDecoder,
}


def load_custom_decoders(folder: str) -> None:
    """Load decoder plugins from *folder* and merge them into CONSOLE_OPTIONS / DECODERS.

    Each .py file in the folder must define:
        CONSOLE_OPTIONS = [('settings_key', 'Human Label', 'decoder_key'), ...]
        DECODERS        = {'decoder_key': MyDecoderClass, ...}

    Keys already present in the built-in registries are silently skipped so
    that calling this function multiple times is safe.
    """
    if not os.path.isdir(folder):
        return

    existing_keys    = {k     for k, _, _  in CONSOLE_OPTIONS}
    existing_decoders = set(DECODERS)

    for fname in sorted(os.listdir(folder)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        path        = os.path.join(folder, fname)
        module_name = f'_tremplin_custom_decoder_{fname[:-3]}'
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod  = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)

            for entry in getattr(mod, 'CONSOLE_OPTIONS', []):
                if entry[0] not in existing_keys:
                    CONSOLE_OPTIONS.append(entry)
                    existing_keys.add(entry[0])

            for key, cls in getattr(mod, 'DECODERS', {}).items():
                if key not in existing_decoders:
                    DECODERS[key] = cls
                    existing_decoders.add(key)

        except Exception as e:
            print(f'[custom decoder] failed to load {fname}: {e}', flush=True)


def make_decoder(console_type: str, cfg: dict) -> ConsoleDecoder:
    # Resolve console key → decoder key, falling back to console_type itself
    # so that existing settings with a direct decoder key still work.
    decoder_key = next(
        (dec for key, _, dec in CONSOLE_OPTIONS if key == console_type),
        console_type,
    )
    cls = DECODERS.get(decoder_key, CTSGen6Decoder)
    return cls(cfg)
