import asyncio
import inspect
import typing as t
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from inspect import Parameter, Signature, isawaitable, iscoroutinefunction
from logging import getLogger

from laza.common.abc import abstractclass
from laza.common.collections import Arguments
from laza.common.functools import Missing, export
from laza.common.typing import Self, typed_signature

from .. import Injectable, InjectionMarker, T_Injectable, T_Injected

if t.TYPE_CHECKING:
    from ..injectors import Injector, InjectorContext


logger = getLogger(__name__)

_POSITIONAL_ONLY = Parameter.POSITIONAL_ONLY
_VAR_POSITIONAL = Parameter.VAR_POSITIONAL
_POSITIONAL_KINDS = frozenset([_POSITIONAL_ONLY, _VAR_POSITIONAL])
_POSITIONAL_OR_KEYWORD = Parameter.POSITIONAL_OR_KEYWORD
_KEYWORD_ONLY = Parameter.KEYWORD_ONLY
_VAR_KEYWORD = Parameter.VAR_KEYWORD
_KEYWORD_KINDS = frozenset(
    [Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY, Parameter.VAR_KEYWORD]
)

_EMPTY = Parameter.empty

_T = t.TypeVar("_T")


@abstractclass
class decorators:
    @staticmethod
    def singleton(func: Callable, ctx: "InjectorContext"):
        lock = ctx.lock()
        value = Missing
        if lock is None:

            def run() -> T_Injected:
                nonlocal func, value
                if value is Missing:
                    value = func()
                return value

        else:

            def run() -> T_Injected:
                nonlocal lock, func, value
                if value is Missing:
                    with lock:
                        if value is Missing:
                            value = func()
                return value

        return run

    @staticmethod
    def resource(func: Callable, ctx: "InjectorContext"):
        lock = ctx.lock()
        value = Missing

        if lock is None:

            def run() -> T_Injected:
                nonlocal func, value, ctx
                if value is Missing:
                    value = ctx.enter(func())
                return value

        else:

            def run() -> T_Injected:
                nonlocal lock, func, value, ctx
                if value is Missing:
                    with lock:
                        if value is Missing:
                            value = ctx.enter(func())
                return value

        return run

    @staticmethod
    def contextmanager(cm, ctx: "InjectorContext"):
        lock = ctx.lock()
        value = Missing

        if lock is None:

            def run():
                nonlocal cm, value, ctx
                if value is Missing:
                    value = ctx.enter(cm)
                return value

        else:

            def run():
                nonlocal cm, value, lock, ctx
                if value is Missing:
                    with lock:
                        if value is Missing:
                            value = ctx.enter(cm)
                return value

        return run


class ParamResolver(t.Generic[_T]):

    __slots__ = (
        "annotation",
        "value",
        "dependency",
        "default",
        "has_value",
        "has_default",
    )
    annotation: t.Any
    value: _T
    default: t.Any
    dependency: Injectable

    def __new__(
        cls: type[Self], value: t.Any = _EMPTY, default=_EMPTY, annotation=_EMPTY
    ) -> Self:
        if isinstance(value, InjectionMarker):
            dependency = value
        elif isinstance(default, InjectionMarker):
            dependency = default
        else:
            dependency = annotation

        self = object.__new__(cls)
        self.annotation = annotation
        self.dependency = dependency
        self.value = value
        self.default = default

        self.has_value = not (value is _EMPTY or isinstance(value, InjectionMarker))
        self.has_default = not (
            default is _EMPTY or isinstance(default, InjectionMarker)
        )

        return self

    def bind(self, injector: "Injector"):
        if not self.has_value:
            dep = self.dependency
            if not dep is _EMPTY and injector.is_provided(dep):
                return dep

    def resolve(self, ctx: "InjectorContext"):
        if self.has_value:
            return self.value, _EMPTY, _EMPTY
        elif self.dependency is _EMPTY:
            return _EMPTY, _EMPTY, self.default if self.has_default else _EMPTY
        elif self.has_default:
            return _EMPTY, ctx.find(self.dependency, default=_EMPTY), self.default
        else:
            return _EMPTY, ctx.find(self.dependency, default=_EMPTY), _EMPTY

    def __repr__(self) -> str:
        value, annotation, default = (
            "..." if x is _EMPTY else x
            for x in (self.value, self.annotation, self.default)
        )
        if isinstance(annotation, type):
            annotation = annotation.__name__

        return f'<{self.__class__.__name__}: {"Any" if annotation == "..." else annotation!s} ={default!s}, {value=!s}>'


@export
class FactoryResolver:

    __slots__ = (
        "factory",
        "arguments",
        "signature",
        "decorators",
        "is_async",
    )

    factory: Callable
    arguments: dict[str, t.Any]
    signature: Signature
    decorators: list[Callable[[Callable], Callable]]

    def __init__(
        self,
        factory: Callable,
        signature: Signature = None,
        *,
        is_async: bool = None,
        arguments: Arguments = None,
        decorators: Sequence[Callable[[Callable, "InjectorContext"], Callable]] = (),
    ):
        self.factory = factory
        self.is_async = iscoroutinefunction(factory) if is_async is None else is_async
        self.signature = signature or typed_signature(factory)
        self.decorators = decorators
        self.arguments = self.parse_arguments(arguments)
        self._post_init()

    def _post_init(self):
        pass

    def __call__(
        self,
        injector: "Injector",
        provides: T_Injectable = None,
    ) -> Callable:
        _args, _kwds, _vals, deps = self.evaluate(injector)
        return self.make_resolver(provides, self.factory, _args, _kwds, _vals), deps

    def parse_arguments(self, arguments: Arguments = None):
        if arguments:
            bound = self.signature.bind_partial(*arguments.args, **arguments.kwargs)
        else:
            bound = self.signature.bind_partial()
        return bound.arguments

    def iter_param_resolvers(self):
        arguments = self.arguments
        for n, p in self.signature.parameters.items():
            if p.kind is _VAR_POSITIONAL:
                p = p.replace(annotation=_EMPTY)
                for v in arguments.get(n, ()):
                    yield n, p, self.make_param_resolver(p, v)
            elif p.kind is _VAR_KEYWORD:
                p = p.replace(annotation=_EMPTY)
                for k, v in arguments.get(n, {}).items():
                    yield k, p, self.make_param_resolver(p, v)
            else:
                yield n, p, self.make_param_resolver(p, arguments.get(n, _EMPTY))

    def make_param_resolver(self, param: Parameter, value=_EMPTY):
        return ParamResolver(value, param.default, param.annotation)

    def evaluate(self, injector: "Injector"):
        args = []
        kwds = []
        vals = {}
        deps = defaultdict(list)

        skip_pos = False
        for n, p, r in self.iter_param_resolvers():
            dep = r.bind(injector)
            if p.kind in _POSITIONAL_KINDS:
                if skip_pos is False:
                    if not dep is None:
                        args.append(r)
                        deps[dep].append(n)
                    elif r.has_value or r.has_default:
                        args.append(r)
                    else:
                        skip_pos = True
                continue
            elif r.has_value:
                vals[n] = r.value
            elif not dep is None:
                kwds.append((n, r))
                deps[dep].append(n)

        return tuple(args), tuple(kwds), vals, dict(deps)

    def iresolve_args(self, args: Iterable[ParamResolver], ctx: "InjectorContext"):
        for r in args:
            v, f, d = r.resolve(ctx)
            if _EMPTY is v is d is f:
                break
            yield v, f, d

    def iresolve_kwds(
        self, kwds: Iterable[tuple[str, ParamResolver]], ctx: "InjectorContext"
    ):
        for n, r in kwds:
            v, f, d = r.resolve(ctx)
            if f is _EMPTY:
                continue
            yield n, f

    def _decorate(self, func, ctx: "InjectorContext"):
        for fn in self.decorators:
            func = fn(func, ctx)
        return func

    def iargs(self, args):
        for v, fn, d in args:
            if not v is _EMPTY:
                yield v
            elif not fn is _EMPTY:
                yield fn()
            else:
                yield d

    async def async_iargs(self, args):
        lst = []
        aws = {}
        i = 0
        for v, fn, d in args:
            if not v is _EMPTY:
                lst.append(v)
            elif not fn is _EMPTY:
                if isawaitable(v := fn()):
                    aws[i] = asyncio.ensure_future(v)
                else:
                    lst.append(v)
            else:
                lst.append(d)
            i += 1

        for i, v in zip(aws, await asyncio.gather(*aws.values())):
            lst.insert(i, v)
        return lst

    def ikwds(self, kwds: Iterable[tuple[str, Callable]], skip=None):
        vals = {}
        if skip:
            for n, fn in kwds:
                if not n in skip:
                    vals[n] = fn()
        else:
            for n, fn in kwds:
                vals[n] = fn()

        return vals

    async def async_ikwds(self, kwds: Iterable[tuple[str, Callable]], skip=None):
        vals = {}
        aws = {}
        if skip:
            for n, fn in kwds:
                if not n in skip:
                    if isawaitable(v := fn()):
                        aws[n] = asyncio.ensure_future(v)
                    else:
                        vals[n] = v
        else:
            for n, fn in kwds:
                if isawaitable(v := fn()):
                  aws[n] = asyncio.ensure_future(v)
                else:
                    vals[n] = v

        aws and vals.update(zip(aws, await asyncio.gather(*aws.values())))

        return vals
      
    def make_resolver(self, provides, func, _args, _kwds, _vals):
        if self.is_async:
            if not self.signature.parameters:
                return self.make_async_plain_resolver(provides, func)
            elif not _args:
                return self.make_async_kwds_resolver(provides, func, _kwds, _vals)
            elif not _kwds:
                return self.make_async_args_resolver(provides, func, _args, _vals)
            else:
                return self.make_async_args_kwds_resolver(provides, func, _args, _kwds, _vals)
        else:
            if not self.signature.parameters:
                return self.make_plain_resolver(provides, func)
            elif not _args:
                return self.make_kwds_resolver(provides, func, _kwds, _vals)
            elif not _kwds:
                return self.make_args_resolver(provides, func, _args, _vals)
            else:
                return self.make_args_kwds_resolver(provides, func, _args, _kwds, _vals)

    def make_plain_resolver(self, provides, func):
        def provider(ctx: "InjectorContext"):
            nonlocal func
            return self._decorate(self.plain_wrap_func(func), ctx)

        return provider

    def make_async_plain_resolver(self, provides, func):
        def provider(ctx: "InjectorContext"):
            nonlocal func
            return self._decorate(self.async_plain_wrap_func(func), ctx)
        return provider

    def make_args_resolver(self, provides, func, _args, vals):
        def provider(ctx: "InjectorContext"):
            nonlocal _args, vals, self
            args = (*self.iresolve_args(_args, ctx),)
            return self._decorate(self.arg_wrap_func(func, args, vals), ctx)

        return provider

    def make_async_args_resolver(self, provides, func, _args, vals):
        def provider(ctx: "InjectorContext"):
            nonlocal _args, vals, self
            args = (*self.iresolve_args(_args, ctx),)
            return self._decorate(self.async_arg_wrap_func(func, args, vals), ctx)
    
        return provider

    def make_kwds_resolver(self, provides, func, _kwds, vals):
        def provider(ctx: "InjectorContext"):
            nonlocal vals, _kwds, self
            kwds = (*self.iresolve_kwds(_kwds, ctx),)
            return self._decorate(self.kwd_wrap_func(func, kwds, vals), ctx)

        return provider

    def make_async_kwds_resolver(self, provides, func, _kwds, vals):
        def provider(ctx: "InjectorContext"):
            nonlocal vals, _kwds, self
            kwds = (*self.iresolve_kwds(_kwds, ctx),)
            return self._decorate(self.async_kwd_wrap_func(func, kwds, vals), ctx)
       
        return provider

    def make_args_kwds_resolver(self, provides, func, _args, _kwds, vals):
        def provider(ctx: "InjectorContext"):
            nonlocal _args, vals, _kwds, self
            args = (*self.iresolve_args(_args, ctx),)
            kwds = (*self.iresolve_kwds(_kwds, ctx),)

            return self._decorate(self.arg_kwd_wrap_func(func, args, kwds, vals), ctx)

        return provider

    def make_async_args_kwds_resolver(self, provides, func, _args, _kwds, vals):
        def provider(ctx: "InjectorContext"):
            nonlocal _args, vals, _kwds, self
            args = (*self.iresolve_args(_args, ctx),)
            kwds = (*self.iresolve_kwds(_kwds, ctx),)

            return self._decorate(self.async_arg_kwd_wrap_func(func, args, kwds, vals), ctx)
    
        return provider

    def plain_wrap_func(self, func):
        return func

    def async_plain_wrap_func(self, func):
        return func

    def arg_wrap_func(self, func, args, vals):
        if vals:
            return lambda: func(*self.iargs(args), **vals)
        else:
            return lambda: func(*self.iargs(args))

    def async_arg_wrap_func(self, func, args, vals):
        if vals:
            async def fn():
                nonlocal self, func, args, vals
                return await func(*(await self.async_iargs(args)), **vals)
        else:
            async def fn():
                nonlocal self, func, args
                return await func(*(await self.async_iargs(args)))
        return fn

    def kwd_wrap_func(self, func, kwds, vals):
        if vals:
            return lambda: func(**vals, **self.ikwds(kwds))
        else:
            return lambda: func(**self.ikwds(kwds))

    def async_kwd_wrap_func(self, func, kwds, vals):
        if vals:
            async def fn():
                nonlocal self, func, kwds, vals
                return await func(**vals, **(await self.async_ikwds(kwds)))
        else:
            async def fn():
                nonlocal self, func, kwds
                return await func(**(await self.async_ikwds(kwds)))
        return fn

    def arg_kwd_wrap_func(self, func, args, kwds, vals):
        if vals:
            return lambda: func(*self.iargs(args), **vals, **self.ikwds(kwds))
        else:
            return lambda: func(*self.iargs(args), **self.ikwds(kwds))

    def async_arg_kwd_wrap_func(self, func, args, kwds, vals):
        if vals:
            async def fn():
                nonlocal self, func, args, kwds, vals
                _args, _kwds = await asyncio.gather(self.async_iargs(args), self.async_ikwds(kwds))
                return await func(*_args, **vals, **_kwds)
        else:
            async def fn():
                nonlocal self, func, args, kwds
                _args, _kwds = await asyncio.gather(self.async_iargs(args), self.async_ikwds(kwds))
                return await func(*_args, **_kwds)
        return fn


class CallableFactoryResolver(FactoryResolver):

    __slots__ = 'is_partial',

    def _post_init(self):
        self.is_partial = False

    def iargs(self, args, a=()):
        if self.is_partial:
            yield from super().iargs(args)
            yield from a
        else:
            yield from a
            yield from super().iargs(args[len(a):])

    async def async_iargs(self, args, a=()):
        if self.is_partial:
            lst = await super().async_iargs(args)
            return (v for seq in (lst, a) for v in seq)
        else:
            lst = await super().async_iargs(args[len(a):])
            return (v for seq in (a, lst) for v in seq)

    def plain_wrap_func(self, func):
        return lambda: func

    def async_plain_wrap_func(self, func):
        return lambda: func

    def arg_wrap_func(self, func, args, vals):
        if vals:
            fn = lambda *a, **kw: func(*self.iargs(args, a), **(vals | kw))
        else:
            fn = lambda *a, **kw: func(*self.iargs(args, a), **kw)
        return lambda: fn

    def async_arg_wrap_func(self, func, args, vals):
        if vals:
            async def fn(*a, **kw):
                nonlocal self, func, args, vals
                return await func(*(await self.async_iargs(args, a)), **(vals | kw))
        else:
            async def fn(*a, **kw):
                nonlocal self, func, args
                return await func(*(await self.async_iargs(args, a)), **kw)
        return lambda: fn

    def kwd_wrap_func(self, func, kwds, vals):
        if vals:
            fn = lambda *a, **kw: func(
                *a, **(kw := vals | kw), **self.ikwds(kwds, kw)
            )
        else:
            fn = lambda *a, **kw: func(*a, **kw, **self.ikwds(kwds, kw))
        return lambda: fn

    def async_kwd_wrap_func(self, func, kwds, vals):
        if vals:
            async def fn(*a, **kw):
                nonlocal self, func, kwds, vals
                return await func(
                    *a, **(kw := vals | kw), **(await self.async_ikwds(kwds, kw))
                )
        else:
            async def fn(*a, **kw):
                nonlocal self, func, kwds
                return await func(*a, **kw, **(await self.async_ikwds(kwds, kw)))
        return lambda: fn

    def arg_kwd_wrap_func(self, func, args, kwds, vals):
        if vals:
            fn = lambda *a, **kw: func(
                *self.iargs(args, a),
                **(kw := vals | kw),
                **self.ikwds(kwds, kw),
            )
        else:
            fn = lambda *a, **kw: func(
                *self.iargs(args, a), **kw, **self.ikwds(kwds, kw)
            )
        return lambda: fn

    def async_arg_kwd_wrap_func(self, func, args, kwds, vals):
        if vals:
            async def fn(*a, **kw):
                nonlocal self, func, args, kwds, vals
                kw = vals | kw
                _args, _kwds = await asyncio.gather(self.async_iargs(args, a), self.async_ikwds(kwds, kw))
                return await func(*_args, **kw, **_kwds)
        else:
            async def fn(*a, **kw):
                nonlocal self, func, args, kwds
                _args, _kwds = await asyncio.gather(self.async_iargs(args, a), self.async_ikwds(kwds, kw))
                return await func(*_args, **kw, **_kwds)
        return lambda: fn

