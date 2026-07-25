def _timing_model_unavailable(*args, **kwargs):
    raise RuntimeError(
        "Hummingbird timing-model support is not installed; "
        "run OpenPARF with timing optimization disabled"
    )


convert = _timing_model_unavailable
load = _timing_model_unavailable
