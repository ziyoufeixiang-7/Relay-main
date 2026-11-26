"""Resolve physical inheritance before compiling a static node graph.

Only channels with frequency curves need this analysis. No hardware operations;
no change to Sweep or to the register-only execution engine.
"""
from collections import deque

from logic.FPGACodec import HWConfig


def resolve_static_nodes(nodes, successors, entry, calibrated_channels):
    """Return expanded physical writes and known fundamental frequencies.

    Entry inherits node 0 (50 Hz, zero AC). Propagate possible field values to a
    fixed point, including loops; array order is not execution order. A single
    precompiled node cannot choose between different inherited amplitudes when
    changing their compensation, so reject that case instead of guessing.
    Keys in this helper are frontend channel/layer/node IDs.
    """
    physical = {node: {int(ch): {int(layer): list(value) for layer, value in layers.items()}
                       for ch, layers in channels.items()} for node, channels in nodes.items()}
    channels = calibrated_channels.intersection(ch for data in physical.values() for ch in data)
    default_freq = HWConfig.ConvertFreqToReg(50) / HWConfig.FREQ
    initial = {(ch, 0): frozenset((default_freq,)) for ch in channels}
    for data in physical.values():
        for ch in channels.intersection(data):
            for layer in data[ch]:
                if layer:
                    initial[ch, layer] = frozenset(((0.0, 0.0),))

    writes = {}
    for node, data in physical.items():
        writes[node] = {
            (ch, layer): frozenset((HWConfig.ConvertFreqToReg(value[1]) / HWConfig.FREQ
                                   if layer == 0 else tuple(value),))
            for ch in channels.intersection(data) for layer, value in data[ch].items()
        }
    incoming = {entry: initial}
    pending, queued = deque((entry,)), {entry}
    while pending:
        node = pending.popleft()
        queued.remove(node)
        outgoing = incoming[node] | writes[node]
        for target in successors.get(node, ()):
            if target not in physical:  # FFFF / terminal
                continue
            previous = incoming.get(target)
            merged = outgoing.copy() if previous is None else {
                key: previous[key] | values for key, values in outgoing.items()
            }
            if previous != merged:
                incoming[target] = merged
                if target not in queued:
                    pending.append(target)
                    queued.add(target)

    frequencies = {}
    for node, data in physical.items():
        before = incoming.get(node, initial)
        after = before | writes[node]
        frequencies[node] = {}
        for ch in channels.intersection(data):
            layers = data[ch]
            possible_freq = after[ch, 0]
            if len(possible_freq) == 1:
                frequencies[node][ch] = next(iter(possible_freq))
            elif any(layer and value[0] for layer, value in layers.items()):
                raise ValueError(f"Node {node}, channel {ch}: inherited frequency is ambiguous; specify layer 0")
            if 0 not in layers or before[ch, 0] == possible_freq:
                continue
            for (source_ch, layer), values in before.items():
                if source_ch != ch or not layer or layer in layers or all(v[0] == 0 for v in values):
                    continue
                if len(values) != 1:
                    raise ValueError(
                        f"Node {node}, channel {ch}, layer {layer}: inherited amplitude/phase is ambiguous "
                        "at a frequency change; specify this layer"
                    )
                layers[layer] = list(next(iter(values)))
    return physical, frequencies
