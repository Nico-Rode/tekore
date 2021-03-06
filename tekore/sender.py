"""
Manipulate the way clients send requests.

.. currentmodule:: tekore.sender
.. autosummary::
   :nosignatures:

   TransientSender
   AsyncTransientSender
   PersistentSender
   AsyncPersistentSender
   SingletonSender
   AsyncSingletonSender
   RetryingSender
   CachingSender

Senders provide different levels of connection persistence across requests
and extend other senders to enable retries on failed requests.
The sender of a :class:`Client` also determines whether synchronous or
asynchronous calls are used to send requests and process responses.

Sender instances are passed to a client at initialisation.

.. code:: python

    from tekore import Spotify, Credentials
    from tekore.sender import PersistentSender, AsyncTransientSender

    Credentials(*conf, sender=PersistentSender())
    Spotify(sender=AsyncTransientSender())

Synchronous senders wrap around the :mod:`requests` library,
while asynchronous senders use :mod:`httpx`.
Senders accept additional keyword arguments to :meth:`requests.Session.send`
or :meth:`httpx.AsyncClient.request` that are passed on each request.

.. code:: python

    from tekore.sender import TransientSender

    proxies = {
        'http': 'http://10.10.10.10:8000',
        'https': 'http://10.10.10.10:8000',
    }
    TransientSender(proxies=proxies)

Custom instances of :class:`requests.Session` or :class:`httpx.AsyncClient`
can also be used.

.. code:: python

    from requests import Session
    from tekore.sender import PresistentSender, SingletonSender

    session = Session()
    session.proxies = proxies

    # Attach the session to a sender
    PersistentSender(session)
    SingletonSender.session = session

Default senders and keyword arguments can be changed.
Note that this requires importing the whole sender module.
:attr:`default_sender_instance` has precedence over :attr:`default_sender_type`.
Using an :class:`ExtendingSender` as the default type will raise an error
as it tries to instantiate itself recursively.
Use :attr:`default_sender_instance` instead.
See also :attr:`default_httpx_kwargs`.

.. code:: python

    from tekore import sender, Spotify

    sender.default_sender_type = sender.PersistentSender
    sender.default_sender_instance = sender.RetryingSender()
    sender.default_requests_kwargs = {'proxies': proxies}

    # Now the following are equal
    Spotify()
    Spotify(
        sender=sender.RetryingSender(
            sender=sender.PersistentSender(proxies=proxies)
        )
    )
"""
import time
import asyncio

from abc import ABC, abstractmethod
from typing import Union, Optional, Type
from warnings import warn
from collections import deque
from urllib.parse import urlencode

from httpx import AsyncClient
from requests import Request, Response, Session


class Sender(ABC):
    """
    Sender interface for requests.
    """
    @abstractmethod
    def send(self, request: Request) -> Response:
        """
        Prepare and send a request.

        Parameters
        ----------
        request
            :class:`Request` to send
        """

    @property
    @abstractmethod
    def is_async(self) -> bool:
        """
        :class:`True` if the sender is asynchronous, :class:`False` otherwise.
        """


default_requests_kwargs: dict = {}
"""
Default keyword arguments to send with in synchronous mode.
Not used when any other keyword arguments are passed in.
"""

default_httpx_kwargs: dict = {}
"""
Default keyword arguments to send with in asynchronous mode.
Not used when any other keyword arguments are passed in.
"""


class SyncSender(Sender, ABC):
    """
    Synchronous request sender base class.
    """
    @property
    def is_async(self) -> bool:
        return False


class AsyncSender(Sender, ABC):
    """
    Asynchronous request sender base class.
    """
    @property
    def is_async(self) -> bool:
        return True


class TransientSender(SyncSender):
    """
    Create a new session for each request.

    Parameters
    ----------
    requests_kwargs
        keyword arguments for :meth:`requests.Session.send`
    """
    def __init__(self, **requests_kwargs):
        self.requests_kwargs = requests_kwargs or default_requests_kwargs

    def send(self, request: Request) -> Response:
        with Session() as sess:
            prepared = sess.prepare_request(request)
            return sess.send(prepared, **self.requests_kwargs)


class AsyncTransientSender(AsyncSender):
    """
    Create a new asynchronous client for each request.

    Parameters
    ----------
    httpx_kwargs
        keyword arguments for :meth:`httpx.AsyncClient.request`
    """
    def __init__(self, **httpx_kwargs):
        self.httpx_kwargs = httpx_kwargs or default_httpx_kwargs

    async def send(self, request: Request) -> Response:
        async with AsyncClient() as client:
            return await client.request(
                request.method,
                request.url,
                data=request.data or None,
                params=request.params or None,
                headers=request.headers,
                **self.httpx_kwargs,
            )


class SingletonSender(SyncSender):
    """
    Use one session for all instances and requests.

    Parameters
    ----------
    requests_kwargs
        keyword arguments for :meth:`requests.Session.send`
    """
    session = Session()

    def __init__(self, **requests_kwargs):
        self.requests_kwargs = requests_kwargs or default_requests_kwargs

    def send(self, request: Request) -> Response:
        prepared = SingletonSender.session.prepare_request(request)
        return SingletonSender.session.send(prepared, **self.requests_kwargs)


class AsyncSingletonSender(AsyncSender):
    """
    Use one client for all instances and requests.

    Parameters
    ----------
    httpx_kwargs
        keyword arguments for :meth:`httpx.AsyncClient.request`
    """
    client = AsyncClient()

    def __init__(self, **httpx_kwargs):
        self.httpx_kwargs = httpx_kwargs or default_httpx_kwargs

    async def send(self, request: Request) -> Response:
        return await AsyncSingletonSender.client.request(
            request.method,
            request.url,
            data=request.data or None,
            params=request.params or None,
            headers=request.headers,
            **self.httpx_kwargs,
        )


class PersistentSender(SyncSender):
    """
    Use a per-instance session to send requests.

    Parameters
    ----------
    session
        :class:`requests.Session` to use when sending requests
    requests_kwargs
        keyword arguments for :meth:`requests.Session.send`
    """
    def __init__(self, session: Session = None, **requests_kwargs):
        self.requests_kwargs = requests_kwargs or default_requests_kwargs
        self.session = session or Session()

    def send(self, request: Request) -> Response:
        prepared = self.session.prepare_request(request)
        return self.session.send(prepared, **self.requests_kwargs)


class AsyncPersistentSender(AsyncSender):
    """
    Use a per-instance client to send requests asynchronously.

    Parameters
    ----------
    session
        :class:`httpx.AsyncClient` to use when sending requests
    httpx_kwargs
        keyword arguments for :meth:`httpx.AsyncClient.request`
    """
    def __init__(self, client: AsyncClient = None, **httpx_kwargs):
        self.httpx_kwargs = httpx_kwargs or default_httpx_kwargs
        self.client = client or AsyncClient()

    async def send(self, request: Request) -> Response:
        return await self.client.request(
            request.method,
            request.url,
            data=request.data or None,
            params=request.params or None,
            headers=request.headers,
            **self.httpx_kwargs,
        )


default_sender_type: Union[Type[SyncSender], Type[AsyncSender]] = TransientSender
"""
Sender to instantiate by default.
"""


class ExtendingSender(Sender, ABC):
    """
    Base class for senders that extend other senders.
    """
    def __init__(self, sender: Optional[Sender]):
        self.sender = sender or default_sender_type()

    @property
    def is_async(self) -> bool:
        return self.sender.is_async


class RetryingSender(ExtendingSender):
    """
    Retry requests if unsuccessful.

    On server errors the set amount of retries are used to resend requests.
    On 429 - Too Many Requests the `Retry-After` header is checked and used
    to wait before requesting again.
    Note that even when the number of retries is set to zero,
    retries based on rate limiting are still performed.

    Parameters
    ----------
    retries
        maximum number of retries on server errors before giving up
    sender
        request sender, :attr:`default_sender_type` instantiated if not specified

    Examples
    --------
    Pass the maximum number of retries to retry failed requests.

    .. code:: python

        RetryingSender(retries=3)

    :class:`RetryingSender` can extend any other sender to provide
    the combined functionality.

    .. code:: python

        RetryingSender(sender=SingletonSender())
    """
    def __init__(self, retries: int = 0, sender: Sender = None):
        super().__init__(sender)
        self.retries = retries

    def send(self, request: Request) -> Response:
        if self.is_async:
            return self._async_send(request)

        tries = self.retries + 1
        delay_seconds = 1

        while tries > 0:
            r = self.sender.send(request)

            if r.status_code == 429:
                seconds = r.headers.get('Retry-After', 1)
                time.sleep(int(seconds) + 1)
            elif r.status_code >= 500 and tries > 1:
                tries -= 1
                time.sleep(delay_seconds)
                delay_seconds *= 2
            else:
                return r

    async def _async_send(self, request: Request) -> Response:
        tries = self.retries + 1
        delay_seconds = 1

        while tries > 0:
            r = await self.sender.send(request)

            if r.status_code == 429:
                seconds = r.headers.get('Retry-After', 1)
                await asyncio.sleep(int(seconds) + 1)
            elif r.status_code >= 500 and tries > 1:
                tries -= 1
                await asyncio.sleep(delay_seconds)
                delay_seconds *= 2
            else:
                return r


class CachingSender(ExtendingSender):
    """
    Cache successful GET requests.

    The Web API provides response headers for caching.
    Resources are cached based on Cache-Control, ETag and Vary headers.
    Thus :class:`CachingSender` can be used with user tokens too.
    Resources marked as private, errors and ``Vary: *`` are not cached.

    When using asynchronous senders, the cache is protected with
    :class:`asyncio.Lock` to prevent concurrent access.
    The lock is instantiated on the first asynchronous call,
    so using only one :func:`asyncio.run` (per sender) is advised.

    Note that if the cache has no maximum size it can grow without limit.
    Use :meth:`CachingSender.clear` to empty the cache.

    Parameters
    ----------
    sender
        request sender, :attr:`default_sender_type` instantiated if not specified
    max_size
        maximum cache size (amount of responses), if specified the least
        recently used response is discarded when the cache would overflow
    """
    def __init__(self, sender: Sender = None, max_size: int = None):
        super().__init__(sender)
        self._max_size = max_size
        self._cache = {}
        self._deque = deque(maxlen=self.max_size)
        self._lock: asyncio.Lock = None

    @property
    def max_size(self) -> Optional[int]:
        """
        Maximum amount of requests stored in the cache.
        """
        return self._max_size

    def clear(self) -> None:
        """
        Clear sender cache.
        """
        self._cache = {}
        self._deque.clear()

    @staticmethod
    def _vary_key(request: Request, vary: Optional[list]):
        if vary is not None:
            return ' '.join(request.headers[k] for k in vary)

    @staticmethod
    def _cc_fresh(item: dict) -> bool:
        return item['expires_at'] > time.time()

    @staticmethod
    def _has_etag(item: dict) -> bool:
        return item['etag'] is not None

    def _is_fresh(self, url, vary_key) -> bool:
        item = self._cache[url][1][vary_key]
        return self._cc_fresh(item) or self._has_etag(item)

    def _delete(self, url, vary_key) -> None:
        item = self._cache[url]
        del item[1][vary_key]
        if not item[1]:
            del item

    def _maybe_save(self, request: Request, response: Response) -> None:
        cc = response.headers.get('Cache-Control', 'private, max-age=0')

        if response.status_code >= 400 or 'private' in cc:
            return

        age = int(cc.split('max-age=')[1].split(',')[0])
        vary = response.headers.get('Vary', None)
        if vary is not None:
            if '*' in vary:
                return
            vary = vary.split(', ')

        # Construct cached response
        cache_item = self._cache.get(response.url, (vary, {}))
        self._cache[response.url] = cache_item

        cached_response = {
            'response': response,
            'expires_at': time.time() + age - 1,
            'etag': response.headers.get('ETag', None),
        }
        vary_key = self._vary_key(request, vary)
        cache_item[1].update({vary_key: cached_response})

        # Manage cache size
        if self.max_size is None:
            return

        # Remove stale items
        if len(self._deque) == self._deque.maxlen:
            deque_items = list(self._deque)
            self._deque.clear()

            for item in deque_items:
                fresh = self._is_fresh(*item)

                if fresh:
                    self._deque.append(item)
                else:
                    self._delete(*item)

        # Remove LRU item
        if len(self._deque) == self._deque.maxlen:
            d_url, d_vary_key = self._deque.popleft()
            self._delete(d_url, d_vary_key)

        self._deque.append((response.url, vary_key))

    def _update_usage(self, item) -> None:
        if self.max_size is None:
            return

        self._deque.remove(item)
        self._deque.append(item)

    def _load(self, request: Request) -> tuple:
        params = ('&' + urlencode(request.params)) if request.params else ''
        url = request.url + params
        item = self._cache.get(url, None)

        if item is None:
            return None, None

        vary_key = self._vary_key(request, item[0])
        cached = item[1].get(vary_key, None)

        if cached is not None:
            response = cached['response']
            deque_item = (url, vary_key)
            if self._cc_fresh(cached):
                self._update_usage(deque_item)
                return response, None
            elif self._has_etag(cached):
                self._update_usage(deque_item)
                return response, cached['etag']
            elif self.max_size is not None:
                self._deque.remove(deque_item)

        return None, None

    def _handle_fresh(self, request, fresh: Response, cached: Response):
        if fresh.status_code == 304:
            return cached
        else:
            self._maybe_save(request, fresh)
            return fresh

    def send(self, request: Request) -> Response:
        if self.is_async:
            return self._async_send(request)

        if request.method.lower() != 'get':
            return self.sender.send(request)

        cached, etag = self._load(request)
        if cached is not None and etag is None:
            return cached
        elif etag is not None:
            request.headers.update(ETag=etag)

        fresh = self.sender.send(request)
        return self._handle_fresh(request, fresh, cached)

    async def _async_send(self, request: Request):
        if request.method.lower() != 'get':
            return await self.sender.send(request)

        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            cached, etag = self._load(request)

        if cached is not None and etag is None:
            return cached
        elif etag is not None:
            request.headers.update(ETag=etag)

        fresh = await self.sender.send(request)
        async with self._lock:
            return self._handle_fresh(request, fresh, cached)


default_sender_instance: Sender = None
"""
Default sender instance to use in clients.
If specified, overrides :attr:`default_sender_type`.
"""


def new_default_sender() -> Sender:
    return default_sender_instance or default_sender_type()


class SenderConflictWarning(RuntimeWarning):
    """Issued when sender arguments to a client are in conflict."""


class Client:
    """
    Base class for clients.

    Parameters
    ----------
    sender
        request sender - If not specified, using :attr:`default_sender_instance`
        is attempted first, then :attr:`default_sender_type` is instantiated.
    asynchronous
        synchronicity requirement - If specified, overrides passed
        sender and defaults if they are in conflict and instantiates
        a transient sender of the requested type
    """
    def __init__(self, sender: Optional[Sender], asynchronous: bool = None):
        new_sender = sender or new_default_sender()

        if new_sender.is_async and asynchronous is False:
            new_sender = TransientSender()
        elif not new_sender.is_async and asynchronous is True:
            new_sender = AsyncTransientSender()

        self.sender = new_sender

        if sender is not None and new_sender.is_async != sender.is_async:
            msg = f'\n{type(sender)} passed but asynchronous={asynchronous}!'
            msg += '\nA sender was instantiated according to `asynchronous`.'
            warn(msg, SenderConflictWarning, stacklevel=3)

    def _send(self, request: Request) -> Response:
        return self.sender.send(request)

    @property
    def is_async(self) -> bool:
        return self.sender.is_async
