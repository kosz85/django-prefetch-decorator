import types
import collections
from django.conf import settings
from django.db.models.loading import get_model
from django.db.models import Model

if not hasattr(settings, 'PREFETCH_ALIASES'):
    settings.PREFETCH_ALIASES = {}


def is_prefetched(obj, attr):
    """Check if attr in obj is prefetched"""
    if attr in getattr(obj, '_prefetched_objects_cache', []):
        return True
    if hasattr(obj, '_'.join(('', attr, 'cache'))):
        return True
    return False


def are_prefetched(obj, attrs):
    """Check if attrs in obj is prefetched"""
    for attr in attrs:
        if is_prefetched(obj, attr) is False:
            return False
    return True


def prefetch(**prefetch_kwargs):
    """
    model:[(db_path__other_path__next_path, func1, func2), func3, path, path]

    <type>=[subfield__subsubfield to prefetch or func with prefetch]
    func can be a func, string (module.submodule.func)
    or a tuple ("app.model", "func_name")

    Collecting prefetches is ultra lazy. We can't do it on model loading
    so there are load_model_call, func_paths_call, and many lambdas
    as normal data among normal strings.
    Fetching this objects is easy, just call obj() when it still returns func,
    it means we couldn't fetch smth. It only seems complicated.

    If some errors occures when running standalone scripts, or admin, we need
    to cache models, it's known django behaviour and circular nightmare:
        # Hack which prevents circular imports
        from django.db.models.loading import cache as model_cache

        if not model_cache.loaded:
                model_cache.get_models()
        # End of hack
    """
    def load_model(x):
        """ loads model from strings like "app_name.ModelName" or from declared
        model aliases in settings.PREFETCH_ALIASES"""
        try:
            if issubclass(x, Model):
                return x
        except TypeError:
            pass
        names = settings.PREFETCH_ALIASES.get(x, x).split('.')
        return get_model('.'.join(names[:-1]), names[-1])

    isiter = lambda x: hasattr(x, '__iter__')  # Check obj is iterable
    load_model_call = lambda x: load_model(x) or (lambda: load_model_call(x))
    func_paths = lambda f, m, p: p and [
        '__'.join((p, i))
        for i in getattr(f, 'get_prefetch', lambda x: [])(m)] or \
        [i for i in getattr(f, 'get_prefetch', lambda x: [])(m)]
    isfunc = lambda x: (isinstance(x, types.FunctionType) or
                        isinstance(x, types.MethodType))

    def load_func(x):
        """loads func or None

        func can be just func
             can be string "module.submodule.func"
             can be tuple or list (model_or_alias, "method")"""
        if isfunc(x):
            return x
        elif type(x) in (tuple, list) and len(x) == 2:
            model = load_model(x[0])
            if not isfunc(model):
                return getattr(model, x[1], None)
        elif type(x) is basestring:
            x = x.split('.')
            i = 1
            l = len(x)
            while i < l:
                try:
                    func = __import__('.'.join(x[:-i]))
                    for submodule in x[1:]:
                        func = getattr(func, submodule)

                    if isfunc(func):
                        return func
                except (ImportError, AttributeError):
                    i += 1
                    continue
            raise ImportError("Can't import prefetched func %s" % '.'.join(x))

    def func_paths_call(f, m, p):
        """Load func or return callable to load it in future"""
        func = load_func(f)
        load_later = lambda: func_paths_call(f, m, p)
        #load_later.__dict__['params'] = (f, m, p)
        if func is None:
            return load_later
        if isfunc(m):
            m = m()
            if isfunc(m):
                return load_later
        ret = func_paths(func, m, p)
        extend = []
        for r in ret:
            if isfunc(r):
                r = r()
                if isfunc(r):
                    extend.extend(r)
                else:
                    return load_later
        ret.extend(extend)
        return ret

    def get_model_from_query_path_call(model, path):
        """Return end model of prefetching path for some model or callable
        model is model
        path is  string "some_field__rel_field__other_field"
        returned model will be model of the "other_field"
        """
        if isfunc(model):
            model = model()
            if isfunc(model):
                return lambda: get_model_from_query_path_call(model, path)
        path = path.split('__')
        while path:
            field = model._meta.get_field_by_name(path[0])[0]
            path = path[1:]
            rel = getattr(field, 'related', None)
            if rel:
                model = rel.parent_model
            else:
                model = field.model
        return model

    def reduce_prefetch(paths):
        """ this merges ["field1__field2", "field1"] into one
        ["field1__field2"] to save memory and cpu
        """
        i = 0
        while i < len(paths):
            incr = True
            if isfunc(paths[i]):
                i += 1
                continue
            j = i
            l = len(paths) - 1
            while j < l:
                j += 1
                if isfunc(paths[j]):
                    continue
                if paths[i].startswith(paths[j]):
                    paths = paths[:j] + paths[j + 1:]
                    l = len(paths) - 1
                elif paths[j].startswith(paths[i]):
                    paths = paths[:i] + paths[i + 1:]
                    incr = False
                    break
            if incr is True:
                i += 1
        return paths

    # Main prefetch engine which collect what to prefetch
    prefetch = collections.defaultdict(list)
    for k, v in prefetch_kwargs.items():
        key_model = load_model_call(k)
        for path in v:
            if isfunc(path):
                extend = func_paths_call(path, key_model, None)
                if isfunc(extend):
                    prefetch[key_model].append(extend)
                else:
                    prefetch[key_model].extend(extend)
            elif isiter(path):
                funcs = path[1:]
                path = path[0]
                model = get_model_from_query_path_call(key_model, path)
                for func in funcs:
                    extend = func_paths_call(func, model, path)
                    if isfunc(extend):
                        prefetch[key_model].append(extend)
                    else:
                        prefetch[key_model].extend(extend)
                prefetch[key_model].append(path)
            else:
                prefetch[key_model].append(path)

    class Prefetch(dict):
        """ Callable dict of prefetch django fields
        Use like:
            some_func.get_prefetch('business')
        """
        def __init__(self, *args, **kwargs):
            self.fully_loaded = False
            super(Prefetch, self).__init__(*args, **kwargs)
            self.load()

        def __call__(self, model):
            """ load prefetches if not fully loaded,
            and return prefetches for chosen model
            usage:
                some_func.get_prefetch('business')
                some_func.get_prefetch('business.Business')
                some_func.get_prefetch('structure.Region')
                """
            self.load()
            prefetch = self.get(load_model(model), [])
            if self.fully_loaded:
                return prefetch
            else:
                return [p for p in prefetch if not isfunc(p)]

        def load(self):
            """ try load prefetches if not fully loaded"""
            if self.fully_loaded:
                return
            self.fully_loaded = True
            rm_key = []  # keys to remove (lambdas)
            for k in self.keys():
                if isfunc(k):
                    new_k = k()  # try load model
                    if not isfunc(new_k):
                        rm_key.append(k)
                        self[new_k] = self[k]
                        prefetch = []
                        rm_func = []  # prefetches to remove (lambdas)
                        for v in self[new_k]:
                            if isfunc(v):
                                new_v = v()  # try fetch prefetches
                                if isfunc(new_v):
                                    self.fully_loaded = False
                                else:
                                    rm_func.append(v)
                                    prefetch.extend(new_v)
                        for r in rm_func:  # remove fetched funcs
                            self[new_k].remove(r)
                        self[new_k].extend(prefetch)
                    else:
                        self.fully_loaded = False
                self[k] = reduce_prefetch(self[k])

            for k in rm_key:  # remove fetched models
                del self[k]

    def decorator(f):
        f.__dict__['get_prefetch'] = Prefetch(prefetch)
        return f

    return decorator
