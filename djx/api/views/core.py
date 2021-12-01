
from abc import abstractmethod
from inspect import getmembers
import io
from types import MethodType
import typing as t
import logging

from functools import partial, update_wrapper
from collections.abc import Hashable, Iterable, Mapping, Callable, Sequence, Collection
from django.core.exceptions import BadRequest
from django.http.response import HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt
from djx.api.abc import Headers

from djx.di.common import Depends, InjectionToken
from djx.di.container import IocContainer
from djx.di.scopes import REQUEST_SCOPE

from ..abc import BodyParser, Args, Body, Kwargs
from djx.common.collections import frozendict

from djx.di import ioc, get_ioc_container
from djx.common.utils import export, lookup_property, text
from djx.schemas import QueryLookupSchema, OrmSchema, Schema, parse_obj_as
from djx.schemas.tools import _get_parsing_type






from ..  import exc, Request
from .config import ViewConfig, ActionConfig
from ..types import HttpMethod, ActionRouteDescriptor, T_HttpMethods, ViewActionFunction, ViewFunction, is_action_func


logger = logging.getLogger(__name__)

_T_Co = t.TypeVar('_T_Co', covariant=True)

_T_Schema = t.TypeVar('_T_Schema', bound=Schema)

_T_Resource = t.TypeVar('_T_Resource', bound='Resource', covariant=True)
_T_View = t.TypeVar('_T_View', bound='View', covariant=True)
_T_Entity = t.TypeVar('_T_Entity', covariant=True)
_T_Data = t.TypeVar('_T_Data', covariant=True)
_T_Payload = t.TypeVar('_T_Payload', covariant=True)

_T_Key = t.TypeVar('_T_Key', str, int, t.SupportsInt, Hashable)


_config_lookup = partial(lookup_property, source='config', read_only=True)




_T_ActionResolveFunc = Callable[['View', Request], ActionConfig]
_T_ActionResolver = Callable[[ViewConfig, Mapping[_T_Co, ActionConfig]], _T_ActionResolveFunc]




# @export()
# class ActionDescriptor(t.Generic[_T_Co]):

#     # __slots__ = 'func', 'conf',

#     func: t.Union[t.Callable[..., _T_Co], None]
#     conf: frozendict[str, t.Any]

#     mapping: MethodMapper


#     def __init__(self, 
#                 methods: T_HttpMethods=..., 
#                 url_path: t.Union[str, t.Pattern, None]=..., 
#                 url_name: t.Union[str, None] = None, *,
#                 detail: bool, 
#                 **config):

#         self.mapping = 
#         self.func = func
#         self.conf = frozendict(conf, **config)

#     # def replace(self, conf: dict[str, t.Any] = frozendict(), /, **config):
#     #     return self.__class__(self.func, self.conf.merge(conf, **config))
        
#     def __set_name__(self, owner, name):
#         vardump(__set_name__=name, __owner__=owner)        
#         if not isinstance(owner, ViewType):
#             raise RuntimeError(f'{self.__class__.__name__} can only be added to ViewTypes not {owner}')

#     def __call__(self, func: t.Callable[..., _T_Co]) -> t.Callable[..., _T_Co]:

#         return self.__class__(func, self.conf)
        


# @export()
# def action(func: t.Union[t.Callable[..., _T_Co], None]=None, 
#         conf: dict[str, t.Any] = frozendict(), /, 
#         **config: t.Any):

#     return ActionDescriptor(func, conf, **config)



def _is_routed_action(attr: ViewActionFunction):
    return ViewActionFunction.get_exis



def _check_attr_name(func, name):
    assert func.__name__ == name, (
        'Expected function (`{func.__name__}`) to match its attribute name '
        '(`{name}`). If using a decorator, ensure the inner function is '
        'decorated with `functools.wraps`, or that `{func.__name__}.__name__` '
        'is otherwise set to `{name}`.').format(func=func, name=name)
    return func




ROOT_ACTIONS = dict({ 
    m.name.lower(): m for m in HttpMethod 
}, list=HttpMethod.GET)


@t.overload
def action(methods: t.Union[T_HttpMethods, None]=None, 
        url_path: t.Union[str, t.Pattern, None] = None, 
        url_name: t.Union[str, None] = None,  
        *,
        detail:bool=...,
        outline:bool=...,
        **config) -> Callable[[ViewActionFunction[_T_View]], ViewActionFunction[_T_View]]:
    ...

@export()
def action(methods: t.Union[T_HttpMethods, None]=None, 
        url_path: t.Union[str, t.Pattern, None]=None, 
        url_name=None, 
        **config):

    # name and suffix are mutually exclusive
    # if 'name' in config and 'suffix' in config:
    #     raise TypeError("`name` and `suffix` are mutually exclusive arguments.")

    def decorator(func: ViewActionFunction[_T_View]):

        # if func.__name__ in ROOT_ACTIONS:
        #     assert not (methods or url_path or url_name), (
        #             f"Cannot set `methods`, `url_path` or `url_name` on the following "
        #             f"methods, as they are reserved for root actions: "
        #             f"{', '.join(f'{a!r}' for a in ROOT_ACTIONS)}"
        #         ) 
        #     func.mapping = MethodMapper(func, ())

        ActionRouteDescriptor(func, methods, url_path=url_path, url_name=url_name, **config)

        # func.url_path = url_path
        # func.url_name = url_name

        # func.config = config

        # Set descriptive arguments for viewsets
        # if 'name' not in config and 'suffix' not in config:
        #     func.config['name'] = text.humanize(func.__name__).capitalize()
        
        return func
    return decorator




@export()
class ViewType(type):

    __config_instance__: t.Final[ViewConfig]
    __local_actions__: t.Final[dict[str, dict[str, t.Any]]] = ...

    def __new__(mcls, name: str, bases: tuple[type], dct: dict, **kwds):
        attrs = dct

        attrs['__config_instance__'] = attrs['config'] = None
    
        if 'Config' not in attrs:
            attrs['Config'] = type('Config', (), kwds)
        elif kwds:
            raise TypeError('cannot use both config keywords and Config at the same time')
        

        cls = super().__new__(mcls, name, bases, attrs)
        if any(isinstance(b, ViewType) for b in bases):
            pass
        
        return cls

    @property
    def __config__(self) -> ViewConfig:
        res = self.__config_instance__
        if res is None:
            res = self.__config_instance__ = self.config = self._create_config_instance_('__config__', '__config_class__')
        return res

    def _create_config_instance_(self, attr, cls_attr):
        cls = ViewConfig.get_class(self, cls_attr)
        assert not issubclass(cls, ActionConfig), (
            f"""View config should not be action config. {attr=}, {cls_attr=}
            {cls.mro()}
        """
        )
        return cls(self, attr, self.Config)

    def get_extra_actions(self: type[_T_View], *, known: Collection[str]=ROOT_ACTIONS) -> list[ViewActionFunction[_T_View]]:
        """
        Get the methods that are marked as an extra ViewSet `@action`.
        """
        return [a for a in self.get_all_action_descriptors() if _is_routed_action(a) or a.__name__ not in known]

    def get_all_action_descriptors(self: type[_T_View]) -> dict[str, ActionRouteDescriptor[_T_View]]:
        """
        Get the methods that are marked as an extra ViewSet `@action`.
        """
        return { 
            r.action: r
                for name, method
                in getmembers(self, is_action_func) if (r := ActionRouteDescriptor.get_existing_descriptor(method))
            }



@export()
class View(t.Generic[_T_Entity], metaclass=ViewType):
    """ResourceManager Object"""
    
    # __slots__ = 'action', 'request', 'args', 'kwargs', '_params', '_ioc', # '__dict__'

    if t.TYPE_CHECKING:
        __config__: t.Final[ViewConfig] = ...

    # config: ViewConfig
    # schemas: AttributeMapping[t.Any, type[_T_Schema]] = _config_lookup('schemas')

    parser: BodyParser

    # ioc: t.Final[IocContainer]
    request: t.Final[Request]
    if t.TYPE_CHECKING:
        config: t.Final[t.Union[ActionConfig, ViewConfig]] = ...


    class Config:
        abstract = True

    @property
    def ioc(self):
        try:
            return self.__dict__['ioc']
        except KeyError:
            return self.__dict__.setdefault('ioc', self.config.ioc.current())

    @ioc.setter
    def ioc(self, val):
        self.__dict__['ioc'] = val
        
    @property
    def handler(self) -> Callable:
        try:
            return self.__dict__['handler']
        except KeyError:
            return self.__dict__.setdefault('handler', self.missing_handler())
    
    @handler.setter
    def handler(self, val):
        self.__dict__['handler'] = val

    @property
    def headers(self) -> Headers:
        try:
            return self.__dict__['headers']
        except KeyError:
            return self.__dict__.setdefault('headers', self._get_default_headers())
        
    @property
    def object(self) -> _T_Entity:
        raise AttributeError('object')

    @property
    def objects(self) -> Iterable[_T_Entity]:
        raise AttributeError('objects')

    @property
    def params(self):
        try:
            return self._params
        except AttributeError:
            self._params = res = self.parse_params()
            return res
        
    
    if t.TYPE_CHECKING:
        def _set_private_attr_(self, name, val):
            ...

    @classmethod
    def as_view(cls, actions: Mapping[str, str], /, **config):

        """
        Because of the way class based views create a closure around the
        instantiated view, we need to totally reimplement `.as_view`,
        and slightly modify the view function that is created and returned.
        """
        
        conf = cls.__config__
        ioc = conf.ioc

        mapping = conf.get_action_mapping(actions, config)

        # actions must not be empty
        if not mapping:
            raise TypeError("The `actions` argument must be provided when "
                            "calling `.as_view()` on a ViewSet. For example "
                            "`.as_view({'get': 'list'})`")

        # token = InjectionToken(f'{cls.__name__}.actions[{" | ".join(dict.fromkeys(a.name for a in actions.values()))}]')

        # ioc.type(token, use=cls, at=REQUEST_SCOPE, kwargs=config)

        # vardump(actions, config, mapping)

        def view(req: Request, *args, **kwargs):
            nonlocal cls, ioc, mapping
            try:
                handler, conf = mapping[req.method]
            except KeyError:
                return HttpResponseNotAllowed(list(mapping), content=b"Method not allowed")
            else:
                inj = ioc.current()
                self: cls = inj[cls]
                self.ioc = inj
                self.config = conf
                if handler is not None:
                    self.handler = getattr(self, handler)
                if conf is not None:
                    self.config = conf
                return self.dispatch(req, *args, **kwargs)

        # take name and docstring from class
        update_wrapper(view, cls, updated=())

        # and possible attributes set by decorators
        # like csrf_exempt from dispatch
        update_wrapper(view, cls.dispatch, assigned=())

        # We need to set these on the view function, so that breadcrumb
        # generation can pick out these bits of information from a
        # resolved URL.
        view.cls = cls
        view.initkwargs = config
        view.actions = actions
        return view

    def dispatch(self, request: Request, *args, **kwargs):
        self.request = request
        self.args = args
        self.kwargs = kwargs
        return self.handler(    ) #(*args, **kwargs)

    def options(self, *args, **kwargs):
        """
        Handler method for HTTP 'OPTIONS' request.
        """
        pass
        # if self.metadata_class is None:
        #     return self.http_method_not_allowed(request, *args, **kwargs)
        # data = self.metadata_class().determine_metadata(request, self)
        # return Response(data, status=status.HTTP_200_OK)

    def missing_handler(self) -> Callable:
        return getattr(self, self.request.method.lower())

    @t.overload
    def get_payload(self, objects: Iterable[_T_Entity], *, many: t.Literal[True]) -> Sequence[_T_Payload]:
        ...

    @t.overload
    def get_payload(self, object: _T_Entity, *, many: t.Literal[False]=False) -> _T_Payload:
        ...

    def get_payload(self, data: t.Union[Iterable[_T_Entity], _T_Entity], *, many: bool=False) -> _T_Payload:
        cls = self.config.the_response_schema
        if many:
            return _get_parsing_type(list[cls]).validate(data)
        else:
            return cls.validate(data)
        
    def abort(self, status=400, errors=None, **kwds):
        raise BadRequest(f'{errors or ""} code={status}')

    def parse_params(self, data=None, *, using: t.Union[type[Schema], None]=None):
        return self.request.GET

    def parse_body(self, body=..., /, **kwds):
        if body is ...:
            body = self.ioc[Body]

        schema = self.config.request_schema
        if isinstance(body, (str, bytes)):
            res = schema.parse_raw(body, **kwds)
        else:
            res = schema.parse_obj(body, **kwds)
        return res

    def _get_default_headers(self):
        return dict(self.config.headers)