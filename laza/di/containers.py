from abc import ABC, abstractmethod
from contextvars import ContextVar
from functools import update_wrapper
from logging import getLogger
import os
from types import FunctionType, GenericAlias
import typing as t

from collections import ChainMap
from collections.abc import Callable, Mapping, Sequence

from laza.common.functools import export
from laza.common.collections import orderedset

from laza.common.functools import cached_property, calling_frame



from .common import (
    Injectable,
    InjectionToken,
    T_Injectable,
    unique_id
)

from .registries import ProviderRegistry

if t.TYPE_CHECKING:
    from .scopes import AbcScope
    from .injectors import Injector, InjectorContext


logger = getLogger(__name__)

_T = t.TypeVar('_T')



@export()
@Injectable.register
class AbcIocContainer(ProviderRegistry, ABC):

    name: str
    requires: orderedset['IocContainer']
    dependants: orderedset['AbcScope']
    shared: bool = True

    def __init__(self, 
                *requires: 'IocContainer',
                name: str=None,
                shared: bool=None):

        if shared is not None:
            self.shared = shared

        self.name = name
        self._setup_requires()
        self._setup_dependants()
        requires and self.requires.update(requires)
        # self[self] = p.InjectorProvider()

    def __str__(self) -> str:
        return f'{self.__class__.__name__}({self.name!r})'

    def __repr__(self) -> str:
        requires = [*self.requires]
        return f'{self.__class__.__name__}({self.name!r}, {requires=!r})'

    @property
    @abstractmethod
    def _context(self) -> 'InjectorContext':
        ...

    def _setup_requires(self):
        self.requires = orderedset()
     
    def _setup_dependants(self):
        self.dependants = orderedset()
     
    def add_dependant(self, scope: 'AbcScope'):
        if (ctx := self._context) and (dctx := scope._context) and dctx != ctx:
            raise ValueError(
                f'InjectorContext conflict in {self} '
                f'{self._context=} and {scope._context}'
            )

        self.dependants.add(scope)
        
        return self

    def flush(self, tag, source=None):
        if source is None:
            for d in self.dependants:
                d.flush(tag, self)
   
    def inject(self, func: Callable[..., _T]=None, **opts):
        def decorator(fn: Callable[..., _T]):
            token = InjectionToken(f'{fn.__module__}.{fn.__qualname__}')
            self.function(fn, token)

            def wrapper(*a, **kw):
                nonlocal self, token
                return self._context.get().make(token, *a, **kw)

            wrapper = update_wrapper(wrapper, fn)
            wrapper.__injection_token__ = token

            return wrapper

        if func is None:
            return decorator
        else:
            return decorator(func)    



@export()
class IocContainer(AbcIocContainer):

    shared: bool = True
    _context: 'InjectorContext' = None

    def __init__(self, 
                *requires: 'IocContainer',
                name: str=None,
                shared: bool=None):

        if not name:
            name = calling_frame(1, globals=True)['__package__']
            name = f'{name}[{unique_id(name)}]'
            
        super().__init__(*requires, name=name, shared=shared)
    
    # @property
    # def _context(self): # -> 'InjectorContext':
    #     if dep := next(iter(self.dependants), None):
    #         return dep._context

    @cached_property
    def repository(self):
        return self._create_repository()
    
    def _create_repository(self):
        return ChainMap({}, *(d.repository for d in self.requires))
 
    def __contains__(self, x):
        if isinstance(x, IocContainer):
            return x is self \
                or x in self.requires \
                or any(x in d for d in self.requires)
        else:
            return super().__contains__(x)
      
    def add_dependant(self, scope: 'AbcScope'):
        super().add_dependant(scope)
        if self._context is None:
            self._context = scope._context
        
        return self