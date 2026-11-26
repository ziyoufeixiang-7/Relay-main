import math
from typing import Tuple, Callable, Awaitable, Optional

Point = Tuple[float, ...]
TestFunc = Callable[[Point], Awaitable[bool]]

async def searchBoundary(pt1: Point, pt2: Point, prec: float, test: TestFunc, mode: int = 0) -> Optional[Point]:
    if (hit1 := await test(pt1)) == await test(pt2): return None
    
    dist = math.hypot(*(b - a for a, b in zip(pt1, pt2)))
    interp = lambda t: tuple(a + t * (b - a) for a, b in zip(pt1, pt2))
    
    if mode == 0:
        lowT, highT = 0.0, 1.0
        while (highT - lowT) * dist > prec:
            if await test(interp(midT := (lowT + highT) / 2.0)) == hit1: 
                lowT = midT
            else: 
                highT = midT
        return interp(highT) 
    else:
        step, t = prec / dist, 0.0
        while (t := t + step) < 1.0:
            if await test(interp(t)) != hit1: 
                return interp(t)
        return None