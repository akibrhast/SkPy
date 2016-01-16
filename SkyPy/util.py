from __future__ import unicode_literals

import re
from collections import Hashable
from functools import partial, wraps

def upper(s):
    """
    Shorthand to uppercase a string, and leave None as None.
    """
    return s if s == None else s.upper()

def noPrefix(s):
    """
    Remove the type prefix from a chat identifier.
    """
    return s if s == None else s.split(":", 1)[1]

def userToId(url):
    """
    Extract the username from a contact URL.
    """
    match = re.search(r"users(/ME/contacts)?/[0-9]+:([A-Za-z0-9\.,_-]+)", url)
    return match.group(2) if match else None

def chatToId(url):
    """
    Extract the conversation ID from a conversation URL.
    """
    match = re.search(r"conversations/([0-9]+:[A-Za-z0-9\.,_-]+(@thread\.skype)?)", url)
    return match.group(1) if match else None

def initAttrs(cls):
    """
    Class decorator: automatically generate an __init__ method that expects args from cls.attrs and stores them.
    """
    def __init__(self, skype=None, raw=None, *args, **kwargs):
        super(cls, self).__init__(skype, raw)
        # Merge args into kwargs based on cls.attrs.
        for i in range(len(args)):
            kwargs[cls.attrs[i]] = args[i]
        # Disallow any unknown kwargs.
        unknown = set(kwargs) - set(cls.attrs)
        if unknown:
            unknownDesc = "an unexpected keyword argument" if len(unknown) == 1 else "unexpected keyword arguments"
            unknownList = ", ".join("'{0}'".format(k) for k in sorted(unknown))
            raise TypeError("TypeError: __init__() got {0} {1}".format(unknownDesc, unknownList))
        # Set each attribute from kwargs, or use the default if not specified.
        for k in cls.attrs:
            setattr(self, k, kwargs.get(k, cls.defaults.get(k)))
    # Add the init method to the class.
    setattr(cls, "__init__", __init__)
    return cls

def convertIds(*types, **kwargs):
    """
    Class decorator: add helper methods to convert identifier properties into SkypeObjs.
    """
    user = kwargs.get("user", ())
    users = kwargs.get("users", ())
    chat = kwargs.get("chat", ())
    def userObj(self, field):
        """
        Retrieve the user referred to in the object.
        """
        userId = getattr(self, field)
        return self.skype.contacts[userId]
    def userObjs(self, field):
        """
        Retrieve all users referred to in the object.
        """
        userIds = getattr(self, field)
        return (self.skype.contacts[id] for id in userIds)
    def chatObj(self, field):
        """
        Retrieve the user referred to in the object.
        """
        return self.skype.getChat(getattr(self, field))
    def attach(cls, method, field, idField):
        """
        Generate the property object and attach it to the class.
        """
        setattr(cls, field, property(partial(method, field=idField)))
    def wrapper(cls):
        # Shorthand identifiers, e.g. @convertIds("user", "chat").
        for type in types:
            if type == "user":
                attach(cls, userObj, "user", "userId")
            elif type == "users":
                attach(cls, userObjs, "users", "userIds")
            elif type == "chat":
                attach(cls, chatObj, "chat", "chatId")
        # Custom field names, e.g. @convertIds(user=["creator"]).
        for field in user:
            attach(cls, userObj, field, "{0}Id".format(field))
        for field in users:
            attach(cls, userObjs, "{0}s.".format(field), "{0}Ids".format(field))
        for field in chat:
            attach(cls, chatObj, field, "{0}Id".format(field))
        return cls
    return wrapper

def cacheResult(fn):
    """
    Decorator: calculate the value on first access, produce the cached value thereafter.

    If the function takes arguments, the cache is a dictionary using all arguments as the key.
    """
    cache = {}
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = args + (str(kwargs),)
        if not all(isinstance(x, Hashable) for x in key):
            # Can't cache with non-hashable args (e.g. a list).
            return fn(*args, **kwargs)
        if key not in cache:
            cache[key] = fn(*args, **kwargs)
        return cache[key]
    # Make cache accessible externally.
    wrapper.cache = cache
    return wrapper

def syncState(fn):
    """
    Decorator: follow state-sync links when provided by an API.

    The function being wrapped must return: url, params, fetch(url, params), process(resp)
    """
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        # The wrapped function should be defined to return these.
        url, params, fetch, process = fn(self, *args, **kwargs)
        if wrapper.state:
            # We have a state link, use that instead of the default URL.
            url = wrapper.state[-1]
            params = {}
        # Store the new state link.
        resp, state = fetch(url, params)
        wrapper.state.append(state)
        return process(resp)
    # Make state links accessible externally.
    wrapper.state = []
    return wrapper

def exhaust(fn, init, *args, **kwargs):
    """
    Repeatedly call a function, starting with init, until false-y, then combine all sets.

    Use with state-synced functions to retrieve all results.
    """
    while True:
        iterRes = fn(*args, **kwargs)
        if iterRes:
            if isinstance(init, dict):
                init.update(iterRes)
            else:
                init += iterRes
        else:
            break
    return init

class SkypeObj(object):
    """
    A basic Skype-related object.  Holds references to the parent Skype instance, and the raw dict from the API.

    The attrs property should be set to the named attributes for that class.

    Use defaults to override None for certain attributes.
    """
    attrs = ()
    defaults = {}
    def __init__(self, skype=None, raw=None):
        """
        Store a reference to the Skype object for later API calls.

        Most implementers don't need to override this method directly, use @initAttrs instead.
        """
        self.skype = skype
        self.raw = raw
    @classmethod
    def rawToFields(cls, raw={}):
        """
        Convert the raw properties of an API response into class fields.  Override to process additional values.
        """
        return {}
    @classmethod
    def fromRaw(cls, skype=None, raw={}):
        """
        Create a new instance based on the raw properties of an API response.
        """
        return cls(skype, raw, **cls.rawToFields(raw))
    def merge(self, other):
        """
        Copy properties from other into self, skipping None values.  Also merges the raw data.
        """
        for attr in self.attrs:
            if not getattr(other, attr, None) == None:
                setattr(self, attr, getattr(other, attr))
        if other.raw:
            if not self.raw:
                self.raw = {}
            self.raw.update(other.raw)
    def __str__(self):
        """
        Pretty print the object, based on the class' attrs parameter.  Produces output something like:

        [<class name>]
        <attribute>: <value>

        Nested objects are indented as needed.
        """
        out = "[{0}]".format(self.__class__.__name__)
        for attr in self.attrs:
            value = getattr(self, attr)
            valStr = ("\n".join(str(i) for i in value) if isinstance(value, list) else str(value))
            out += "\n{0}{1}: {2}".format(attr[0].upper(), attr[1:], valStr.replace("\n", "\n  " + (" " * len(attr))))
        return out
    def __repr__(self):
        """
        Dump properties of the object into a Python-like statement, based on the class' attrs parameter.

        The resulting string is an expression that should evaluate to a similar object, minus Skype connection.
        """
        reprs = ", ".join("{0}={1}".format(k, repr(getattr(self, k))) for k in self.attrs)
        return "{0}({1})".format(self.__class__.__name__, reprs)

class SkypeObjs(SkypeObj):
    """
    A basic Skype collection.  Acts as a container for objects of a given type.
    """
    def __init__(self, skype=None):
        super(SkypeObjs, self).__init__(skype)
        self.synced = False
        self.cache = {}
    def __getitem__(self, key):
        """
        Provide key lookups for items in the cache.  Subclasses may override this to handle not-yet-cached objects.
        """
        if not self.synced:
            self.sync()
        return self.cache[key]
    def __iter__(self):
        """
        Create an iterator for all objects (not their keys) in this collection.
        """
        if not self.synced:
            self.sync()
        for id in sorted(self.cache):
            yield self.cache[id]
    def merge(self, obj):
        """
        Add a given object to the cache, or update an existing entry to include more fields.
        """
        if obj.id in self.cache:
            self.cache[obj.id].merge(obj)
        else:
            self.cache[obj.id] = obj
        return self.cache[obj.id]

class SkypeException(Exception):
    """
    A generic Skype-related exception.
    """
    pass

class SkypeApiException(SkypeException):
    """
    An exception thrown for errors specific to external API calls.

    Args will usually be of the form (message, response).
    """
    pass
