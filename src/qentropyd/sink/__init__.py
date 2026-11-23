"""Linux kernel RNG sink interfaces."""

from qentropyd.sink.linux_rng import (
    RNDADDENTROPY,
    LinuxRngSink,
    RngSinkError,
    pack_rand_pool_info,
    read_kernel_entropy_avail,
    read_kernel_poolsize,
)

__all__ = [
    "RNDADDENTROPY",
    "LinuxRngSink",
    "RngSinkError",
    "pack_rand_pool_info",
    "read_kernel_entropy_avail",
    "read_kernel_poolsize",
]
