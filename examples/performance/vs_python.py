from pprint import pprint
from time import time
from typing import Union
import pytest

from laza.di.injectors import Injector, inject, wire


from libs.di.examples.performance._benchmarkutil import Benchmark, Timer




class Foo:
     def __init__(self) -> None:
        pass

    
class Bar:
    
    def __init__(self, foo: Foo, /) -> None:
        assert isinstance(foo, Foo)

     
class Baz:
    
    def __init__(self, bar: Bar, /) -> None:
        assert isinstance(bar, Bar)


 
class FooBar:
    
    def __init__(self, foo: Foo, bar: Bar, /) -> None:
        assert isinstance(foo, Foo)
        assert isinstance(bar, Bar)



class FooBarBaz:
    
    def __init__(self, foo: Foo, bar: Bar, baz: Baz, /) -> None:
        assert isinstance(foo, Foo)
        assert isinstance(bar, Bar)
        assert isinstance(baz, Baz)
    



class Service:
    
    def __init__(self, foo: Foo, bar: Bar, baz: Baz, /, foobar: FooBar, foobarbaz: FooBarBaz) -> None:
        assert isinstance(foo, Foo)
        assert isinstance(bar, Bar)
        assert isinstance(baz, Baz)
        assert isinstance(foobar, FooBar)
        assert isinstance(foobarbaz, FooBarBaz)




ioc = Injector() 

ioc.factory(Foo)#.singleton()
ioc.factory(Bar).singleton()
ioc.factory(Baz).singleton()
ioc.factory(FooBar)#.singleton()
ioc.factory(FooBarBaz)#.singleton()
ioc.factory(Service)#.singleton()


@inject
def inject_1(baz: Baz, /):
    assert isinstance(baz, Baz)
    return baz

@inject
def inject_2(foo: Foo, bar: Bar, baz: Baz):
    assert isinstance(foo, Foo)
    assert isinstance(bar, Bar)
    assert isinstance(baz, Baz)
    return foo, bar, baz

@inject
def inject_3(foobar: FooBar, foobarbaz: FooBarBaz, /, service: Service):
    assert isinstance(foobar, FooBar)
    assert isinstance(foobarbaz, FooBarBaz)
    assert isinstance(service, Service)
    return foobar, foobarbaz, service



_tap = lambda x=True: x and None
mkfoo = lambda: Foo()
mkbar = lambda: Bar(mkfoo())
mkbaz = lambda: Baz(mkbar())
mkfoobar = lambda: FooBar(mkfoo(), mkbar())
mkfoobarbaz = lambda: FooBarBaz(mkfoo(), mkbar(), mkbaz())
mkservice = lambda: Service(mkfoo(), mkbar(), mkbaz(), mkfoobar(), mkfoobarbaz())

mkinject_1 = lambda: inject_1.__wrapped__(mkbaz())
mkinject_2 = lambda: inject_2.__wrapped__(mkfoo(), mkbar(), mkbaz())
mkinject_3 = lambda: inject_3.__wrapped__(mkfoobar(), mkfoobarbaz(), mkservice())

_n = int(.5e6)

with Timer() as tm:
    with wire(ioc) as ctx:
        
        # [ctx[d]() for d in (Foo, Bar, Baz, FooBar, FooBarBaz, Service)]
        
        bfoo = Benchmark('Foo.', _n).run(PY=mkfoo, DI=ctx[Foo])
        bbar = Benchmark('Bar.', _n).run(PY=mkbar, DI=ctx[Bar])
        bbaz = Benchmark('Baz.', _n).run(PY=mkbaz, DI=ctx[Baz])
        
        bench = Benchmark(str(Foo | Bar | Baz), _n)
        bench |= bfoo | bbar | bbaz 
        print(bench, '\n')

        bfoobar = Benchmark('FooBar.', _n).run(PY=mkfoobar, DI=ctx[FooBar])
        bfoobarbaz = Benchmark('FooBarBaz.', _n).run(PY=mkfoobarbaz, DI=ctx[FooBarBaz])
        bservice = Benchmark('Service.', _n).run(PY=mkservice, DI=ctx[Service])

        bench = Benchmark(str(FooBar | FooBarBaz | Service), _n)
        bench |= bfoobar | bfoobarbaz | bservice
        print(bench, '\n')

        binject_1 = Benchmark('inject_1.', _n).run(PY=mkinject_1, DI=inject_1)
        binject_2 = Benchmark('inject_2.', _n).run(PY=mkinject_2, DI=inject_2)
        binject_3 = Benchmark('inject_3.', _n).run(PY=mkinject_3, DI=inject_3)


        bench = Benchmark('INJECT', _n)
        bench |= binject_1 | binject_2 | binject_3
        print(bench, '\n')
        

print(f'TOTAL: {tm}')
