from threading import Lock
import typing as t
from logging import getLogger

from typing_extensions import Self

import attr


from . import Injectable, InjectionMarker
from ._common import private_setattr
from ._common.collections import frozendict
from .providers import Provider
from .providers.util import ProviderRegistry

logger = getLogger(__name__)




@InjectionMarker.register
@private_setattr
@attr.s(slots=True, frozen=True, repr=False, cmp=False)
class Container(frozendict[Injectable, list[Provider]], ProviderRegistry):

    __id = 0
    __lock = Lock()

    id: int = attr.ib(init=False)
    @id.default
    def _init_id(self):
        with self.__class__.__lock:
            self.__class__.__id += 1
            return self.__class__.__id

    name: str = attr.ib(default='<anonymous>')
    _included: frozendict[Self, Self] = attr.ib(factory=frozendict, init=False, repr=False) 
    __setdefault: t.Callable[..., list[Provider]] = dict[Injectable, list[Provider]].setdefault

    @property
    def included(self):
        return self._included.keys()

    def includes(self, other: Self):
        return other is self \
            or other in self._included \
            or any(c.includes(other) for c in self._included)

    def include(self, *containers: "Container") -> Self:
        self.__setattr(_included=self._included | dict.fromkeys(containers))
        return self
    
    def _dro_entries_(self, source: Self=None):
        yield source or self, self
        if self._included:
            it = reversed(self._included)
            prev = next(it)
            yield from prev._dro_entries_(self)
            for c in it:
                yield prev, c
                yield from c._dro_entries_(self)
                prev = c

    def __setitem__(self, abstract: Injectable, provider: Provider) -> Self:
        if pro :=  provider.set_container(self):
            stack = self.__setdefault(abstract, [])
            pro in stack or stack.append(pro)

    def __missing__(self, key):
        return ()

    def __bool__(self):
        return True
    
    def __eq__(self, o) -> bool:
        return o is self or (False if isinstance(o, Container) else NotImplemented)

    def __ne__(self, o) -> bool:
        return not o is self or (True if isinstance(o, Container) else NotImplemented)

    def __hash__(self):
        return hash(self.name)

    def __str__(self) -> str:
        return str(self.name) # f'{self.__class__.__qualname__}({self.id}, {self.name!r})'

    __repr__ = __str__

