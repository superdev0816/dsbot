# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2017 Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

from .user import User
from .invite import Invite
from .object import Object
from .errors import *
from .permissions import Permissions, PermissionOverwrite
from .enums import ChannelType, Status
from .gateway import *
from .emoji import Emoji
from .http import HTTPClient
from .state import ConnectionState
from . import utils, compat

import asyncio
import aiohttp
import websockets

import logging, traceback
import sys, re, io, enum
import itertools
import datetime
from collections import namedtuple
from os.path import split as path_split

PY35 = sys.version_info >= (3, 5)
log = logging.getLogger(__name__)

AppInfo = namedtuple('AppInfo', 'id name description icon owner')
WaitedReaction = namedtuple('WaitedReaction', 'reaction user')

def app_info_icon_url(self):
    """Retrieves the application's icon_url if it exists. Empty string otherwise."""
    if not self.icon:
        return ''

    return 'https://cdn.discordapp.com/app-icons/{0.id}/{0.icon}.jpg'.format(self)

AppInfo.icon_url = property(app_info_icon_url)

class WaitForType(enum.Enum):
    message  = 0
    reaction = 1

class Client:
    """Represents a client connection that connects to Discord.
    This class is used to interact with the Discord WebSocket and API.

    A number of options can be passed to the :class:`Client`.

    .. _deque: https://docs.python.org/3.4/library/collections.html#collections.deque
    .. _event loop: https://docs.python.org/3/library/asyncio-eventloops.html
    .. _connector: http://aiohttp.readthedocs.org/en/stable/client_reference.html#connectors
    .. _ProxyConnector: http://aiohttp.readthedocs.org/en/stable/client_reference.html#proxyconnector

    Parameters
    ----------
    max_messages : Optional[int]
        The maximum number of messages to store in :attr:`messages`.
        This defaults to 5000. Passing in `None` or a value less than 100
        will use the default instead of the passed in value.
    loop : Optional[event loop].
        The `event loop`_ to use for asynchronous operations. Defaults to ``None``,
        in which case the default event loop is used via ``asyncio.get_event_loop()``.
    connector : aiohttp.BaseConnector
        The `connector`_ to use for connection pooling. Useful for proxies, e.g.
        with a `ProxyConnector`_.
    shard_id : Optional[int]
        Integer starting at 0 and less than shard_count.
    shard_count : Optional[int]
        The total number of shards.
    fetch_offline_members: bool
        Indicates if :func:`on_ready` should be delayed to fetch all offline
        members from the guilds the bot belongs to. If this is ``False``\, then
        no offline members are received and :meth:`request_offline_members`
        must be used to fetch the offline members of the guild.

    Attributes
    -----------
    email
        The email used to login. This is only set if login is successful,
        otherwise it's None.
    ws
        The websocket gateway the client is currently connected to. Could be None.
    loop
        The `event loop`_ that the client uses for HTTP requests and websocket operations.
    """
    def __init__(self, *, loop=None, **options):
        self.ws = None
        self.email = None
        self.loop = asyncio.get_event_loop() if loop is None else loop
        self._listeners = []
        self.shard_id = options.get('shard_id')
        self.shard_count = options.get('shard_count')

        connector = options.pop('connector', None)
        self.http = HTTPClient(connector, loop=self.loop)

        self.connection = ConnectionState(dispatch=self.dispatch, chunker=self._chunker,
                                          syncer=self._syncer, http=self.http, loop=self.loop, **options)

        self.connection.shard_count = self.shard_count
        self._closed = asyncio.Event(loop=self.loop)
        self._is_logged_in = asyncio.Event(loop=self.loop)
        self._is_ready = asyncio.Event(loop=self.loop)

        # if VoiceClient.warn_nacl:
        #     VoiceClient.warn_nacl = False
        #     log.warning("PyNaCl is not installed, voice will NOT be supported")

    # internals

    @asyncio.coroutine
    def _syncer(self, guilds):
        yield from self.ws.request_sync(guilds)

    @asyncio.coroutine
    def _chunker(self, guild):
        if hasattr(guild, 'id'):
            guild_id = guild.id
        else:
            guild_id = [s.id for s in guild]

        payload = {
            'op': 8,
            'd': {
                'guild_id': guild_id,
                'query': '',
                'limit': 0
            }
        }

        yield from self.ws.send_as_json(payload)

    def handle_reaction_add(self, reaction, user):
        removed = []
        for i, (condition, future, event_type) in enumerate(self._listeners):
            if event_type is not WaitForType.reaction:
                continue

            if future.cancelled():
                removed.append(i)
                continue

            try:
                result = condition(reaction, user)
            except Exception as e:
                future.set_exception(e)
                removed.append(i)
            else:
                if result:
                    future.set_result(WaitedReaction(reaction, user))
                    removed.append(i)


        for idx in reversed(removed):
            del self._listeners[idx]

    def handle_message(self, message):
        removed = []
        for i, (condition, future, event_type) in enumerate(self._listeners):
            if event_type is not WaitForType.message:
                continue

            if future.cancelled():
                removed.append(i)
                continue

            try:
                result = condition(message)
            except Exception as e:
                future.set_exception(e)
                removed.append(i)
            else:
                if result:
                    future.set_result(message)
                    removed.append(i)


        for idx in reversed(removed):
            del self._listeners[idx]

    def handle_ready(self):
        self._is_ready.set()

    def _resolve_invite(self, invite):
        if isinstance(invite, Invite) or isinstance(invite, Object):
            return invite.id
        else:
            rx = r'(?:https?\:\/\/)?discord\.gg\/(.+)'
            m = re.match(rx, invite)
            if m:
                return m.group(1)
        return invite

    @property
    def user(self):
        """Optional[:class:`ClientUser`]: Represents the connected client. None if not logged in."""
        return self.connection.user

    @property
    def guilds(self):
        """List[:class:`Guild`]: The guilds that the connected client is a member of."""
        return self.connection.guilds

    @property
    def private_channels(self):
        """List[:class:`abc.PrivateChannel`]: The private channels that the connected client is participating on."""
        return self.connection.private_channels

    @property
    def messages(self):
        """A deque_ of :class:`Message` that the client has received from all
        guilds and private messages.

        The number of messages stored in this deque is controlled by the
        ``max_messages`` parameter.
        """
        return self.connection.messages

    @property
    def voice_clients(self):
        """List[:class:`VoiceClient`]: Represents a list of voice connections."""
        return self.connection.voice_clients

    @asyncio.coroutine
    def _run_event(self, coro, event_name, *args, **kwargs):
        try:
            yield from coro(*args, **kwargs)
        except asyncio.CancelledError:
            pass
        except Exception:
            try:
                yield from self.on_error(event_name, *args, **kwargs)
            except asyncio.CancelledError:
                pass

    def dispatch(self, event, *args, **kwargs):
        log.debug('Dispatching event {}'.format(event))
        method = 'on_' + event
        handler = 'handle_' + event

        try:
            actual_handler = getattr(self, handler)
        except AttributeError:
            pass
        else:
            actual_handler(*args, **kwargs)

        try:
            coro = getattr(self, method)
        except AttributeError:
            pass
        else:
            compat.create_task(self._run_event(coro, method, *args, **kwargs), loop=self.loop)

    @asyncio.coroutine
    def on_error(self, event_method, *args, **kwargs):
        """|coro|

        The default error handler provided by the client.

        By default this prints to ``sys.stderr`` however it could be
        overridden to have a different implementation.
        Check :func:`discord.on_error` for more details.
        """
        print('Ignoring exception in {}'.format(event_method), file=sys.stderr)
        traceback.print_exc()

    @asyncio.coroutine
    def request_offline_members(self, *guilds):
        """|coro|

        Requests previously offline members from the guild to be filled up
        into the :attr:`Guild.members` cache. This function is usually not
        called. It should only be used if you have the ``fetch_offline_members``
        parameter set to ``False``.

        When the client logs on and connects to the websocket, Discord does
        not provide the library with offline members if the number of members
        in the guild is larger than 250. You can check if a guild is large
        if :attr:`Guild.large` is ``True``.

        Parameters
        -----------
        \*guilds
            An argument list of guilds to request offline members for.

        Raises
        -------
        InvalidArgument
            If any guild is unavailable or not large in the collection.
        """
        if any(not g.large or g.unavailable for g in guilds):
            raise InvalidArgument('An unavailable or non-large guild was passed.')

        yield from self.connection.request_offline_members(guilds)

    # login state management

    @asyncio.coroutine
    def login(self, token, *, bot=True):
        """|coro|

        Logs in the client with the specified credentials.

        This function can be used in two different ways.

        Parameters
        -----------
        token: str
            The authentication token. Do not prefix this token with
            anything as the library will do it for you.
        bot: bool
            Keyword argument that specifies if the account logging on is a bot
            token or not.

        Raises
        ------
        LoginFailure
            The wrong credentials are passed.
        HTTPException
            An unknown HTTP related error occurred,
            usually when it isn't 200 or the known incorrect credentials
            passing status code.
        """

        log.info('logging in using static token')
        data = yield from self.http.static_login(token, bot=bot)
        self.email = data.get('email', None)
        self.connection.is_bot = bot
        self._is_logged_in.set()

    @asyncio.coroutine
    def logout(self):
        """|coro|

        Logs out of Discord and closes all connections.
        """
        yield from self.close()
        self._is_logged_in.clear()

    @asyncio.coroutine
    def connect(self):
        """|coro|

        Creates a websocket connection and lets the websocket listen
        to messages from discord.

        Raises
        -------
        GatewayNotFound
            If the gateway to connect to discord is not found. Usually if this
            is thrown then there is a discord API outage.
        ConnectionClosed
            The websocket connection has been terminated.
        """
        self.ws = yield from DiscordWebSocket.from_client(self)

        while not self.is_closed:
            try:
                yield from self.ws.poll_event()
            except (ReconnectWebSocket, ResumeWebSocket) as e:
                resume = type(e) is ResumeWebSocket
                log.info('Got ' + type(e).__name__)
                self.ws = yield from DiscordWebSocket.from_client(self, shard_id=self.shard_id,
                                                                        session=self.ws.session_id,
                                                                        sequence=self.ws.sequence,
                                                                        resume=resume)
            except ConnectionClosed as e:
                yield from self.close()
                if e.code != 1000:
                    raise

    @asyncio.coroutine
    def close(self):
        """|coro|

        Closes the connection to discord.
        """
        if self.is_closed:
            return

        for voice in list(self.voice_clients):
            try:
                yield from voice.disconnect()
            except:
                # if an error happens during disconnects, disregard it.
                pass

            self.connection._remove_voice_client(voice.guild.id)

        if self.ws is not None and self.ws.open:
            yield from self.ws.close()


        yield from self.http.close()
        self._closed.set()
        self._is_ready.clear()

    @asyncio.coroutine
    def start(self, *args, **kwargs):
        """|coro|

        A shorthand coroutine for :meth:`login` + :meth:`connect`.
        """
        yield from self.login(*args, **kwargs)
        yield from self.connect()

    def run(self, *args, **kwargs):
        """A blocking call that abstracts away the `event loop`_
        initialisation from you.

        If you want more control over the event loop then this
        function should not be used. Use :meth:`start` coroutine
        or :meth:`connect` + :meth:`login`.

        Roughly Equivalent to: ::

            try:
                loop.run_until_complete(start(*args, **kwargs))
            except KeyboardInterrupt:
                loop.run_until_complete(logout())
                # cancel all tasks lingering
            finally:
                loop.close()

        Warning
        --------
        This function must be the last function to call due to the fact that it
        is blocking. That means that registration of events or anything being
        called after this function call will not execute until it returns.
        """

        try:
            self.loop.run_until_complete(self.start(*args, **kwargs))
        except KeyboardInterrupt:
            self.loop.run_until_complete(self.logout())
            pending = asyncio.Task.all_tasks(loop=self.loop)
            gathered = asyncio.gather(*pending, loop=self.loop)
            try:
                gathered.cancel()
                self.loop.run_until_complete(gathered)

                # we want to retrieve any exceptions to make sure that
                # they don't nag us about it being un-retrieved.
                gathered.exception()
            except:
                pass
        finally:
            self.loop.close()

        # properties

    @property
    def is_logged_in(self):
        """bool: Indicates if the client has logged in successfully."""
        return self._is_logged_in.is_set()

    @property
    def is_closed(self):
        """bool: Indicates if the websocket connection is closed."""
        return self._closed.is_set()

    # helpers/getters

    @property
    def users(self):
        """Returns a list of all the :class:`User` the bot can see."""
        return list(self.connection._users.values())

    def get_channel(self, id):
        """Returns a :class:`abc.GuildChannel` or :class:`abc.PrivateChannel` with the following ID.

        If not found, returns None.
        """
        return self.connection.get_channel(id)

    def get_guild(self, id):
        """Returns a :class:`Guild` with the given ID. If not found, returns None."""
        return self.connection._get_guild(id)

    def get_user(self, id):
        """Returns a :class:`User` with the given ID. If not found, returns None."""
        return self.connection.get_user(id)

    def get_all_emojis(self):
        """Returns a generator with every :class:`Emoji` the client can see."""
        for guild in self.guilds:
            for emoji in guild.emojis:
                yield emoji

    def get_all_channels(self):
        """A generator that retrieves every :class:`Channel` the client can 'access'.

        This is equivalent to: ::

            for guild in client.guilds:
                for channel in guild.channels:
                    yield channel

        Note
        -----
        Just because you receive a :class:`Channel` does not mean that
        you can communicate in said channel. :meth:`Channel.permissions_for` should
        be used for that.
        """

        for guild in self.guilds:
            for channel in guild.channels:
                yield channel

    def get_all_members(self):
        """Returns a generator with every :class:`Member` the client can see.

        This is equivalent to: ::

            for guild in client.guilds:
                for member in guild.members:
                    yield member

        """
        for guild in self.guilds:
            for member in guild.members:
                yield member

    # listeners/waiters

    @asyncio.coroutine
    def wait_until_ready(self):
        """|coro|

        This coroutine waits until the client is all ready. This could be considered
        another way of asking for :func:`discord.on_ready` except meant for your own
        background tasks.
        """
        yield from self._is_ready.wait()

    @asyncio.coroutine
    def wait_until_login(self):
        """|coro|

        This coroutine waits until the client is logged on successfully. This
        is different from waiting until the client's state is all ready. For
        that check :func:`discord.on_ready` and :meth:`wait_until_ready`.
        """
        yield from self._is_logged_in.wait()

    @asyncio.coroutine
    def wait_for_message(self, timeout=None, *, author=None, channel=None, content=None, check=None):
        """|coro|

        Waits for a message reply from Discord. This could be seen as another
        :func:`discord.on_message` event outside of the actual event. This could
        also be used for follow-ups and easier user interactions.

        The keyword arguments passed into this function are combined using the logical and
        operator. The ``check`` keyword argument can be used to pass in more complicated
        checks and must be a regular function (not a coroutine).

        The ``timeout`` parameter is passed into `asyncio.wait_for`_. By default, it
        does not timeout. Instead of throwing ``asyncio.TimeoutError`` the coroutine
        catches the exception and returns ``None`` instead of a :class:`Message`.

        If the ``check`` predicate throws an exception, then the exception is propagated.

        This function returns the **first message that meets the requirements**.

        .. _asyncio.wait_for: https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for

        Examples
        ----------

        Basic example:

        .. code-block:: python
            :emphasize-lines: 5

            @client.event
            async def on_message(message):
                if message.content.startswith('$greet'):
                    await message.channel.send('Say hello')
                    msg = await client.wait_for_message(author=message.author, content='hello')
                    await message.channel.send('Hello.')

        Asking for a follow-up question:

        .. code-block:: python
            :emphasize-lines: 6

            @client.event
            async def on_message(message):
                if message.content.startswith('$start'):
                    await message.channel.send('Type $stop 4 times.')
                    for i in range(4):
                        msg = await client.wait_for_message(author=message.author, content='$stop')
                        fmt = '{} left to go...'
                        await message.channel.send(fmt.format(3 - i))

                    await message.channel.send('Good job!')

        Advanced filters using ``check``:

        .. code-block:: python
            :emphasize-lines: 9

            @client.event
            async def on_message(message):
                if message.content.startswith('$cool'):
                    await message.channel.send('Who is cool? Type $name namehere')

                    def check(msg):
                        return msg.content.startswith('$name')

                    message = await client.wait_for_message(author=message.author, check=check)
                    name = message.content[len('$name'):].strip()
                    await message.channel.send('{} is cool indeed'.format(name))


        Parameters
        -----------
        timeout : float
            The number of seconds to wait before returning ``None``.
        author : :class:`Member` or :class:`User`
            The author the message must be from.
        channel : :class:`Channel` or :class:`PrivateChannel` or :class:`Object`
            The channel the message must be from.
        content : str
            The exact content the message must have.
        check : function
            A predicate for other complicated checks. The predicate must take
            a :class:`Message` as its only parameter.

        Returns
        --------
        :class:`Message`
            The message that you requested for.
        """

        def predicate(message):
            result = True
            if author is not None:
                result = result and message.author == author

            if content is not None:
                result = result and message.content == content

            if channel is not None:
                result = result and message.channel.id == channel.id

            if callable(check):
                # the exception thrown by check is propagated through the future.
                result = result and check(message)

            return result

        future = compat.create_future(self.loop)
        self._listeners.append((predicate, future, WaitForType.message))
        try:
            message = yield from asyncio.wait_for(future, timeout, loop=self.loop)
        except asyncio.TimeoutError:
            message = None
        return message


    @asyncio.coroutine
    def wait_for_reaction(self, emoji=None, *, user=None, timeout=None, message=None, check=None):
        """|coro|

        Waits for a message reaction from Discord. This is similar to :meth:`wait_for_message`
        and could be seen as another :func:`on_reaction_add` event outside of the actual event.
        This could be used for follow up situations.

        Similar to :meth:`wait_for_message`, the keyword arguments are combined using logical
        AND operator. The ``check`` keyword argument can be used to pass in more complicated
        checks and must a regular function taking in two arguments, ``(reaction, user)``. It
        must not be a coroutine.

        The ``timeout`` parameter is passed into asyncio.wait_for. By default, it
        does not timeout. Instead of throwing ``asyncio.TimeoutError`` the coroutine
        catches the exception and returns ``None`` instead of a the ``(reaction, user)``
        tuple.

        If the ``check`` predicate throws an exception, then the exception is propagated.

        The ``emoji`` parameter can be either a :class:`Emoji`, a ``str`` representing
        an emoji, or a sequence of either type. If the ``emoji`` parameter is a sequence
        then the first reaction emoji that is in the list is returned. If ``None`` is
        passed then the first reaction emoji used is returned.

        This function returns the **first reaction that meets the requirements**.

        Examples
        ---------

        Basic Example:

        .. code-block:: python

            @client.event
            async def on_message(message):
                if message.content.startswith('$react'):
                    msg = await message.channel.send('React with thumbs up or thumbs down.')
                    res = await client.wait_for_reaction(['\N{THUMBS UP SIGN}', '\N{THUMBS DOWN SIGN}'], message=msg)
                    await message.channel.send('{0.user} reacted with {0.reaction.emoji}!'.format(res))

        Checking for reaction emoji regardless of skin tone:

        .. code-block:: python

            @client.event
            async def on_message(message):
                if message.content.startswith('$react'):
                    msg = await message.channel.send('React with thumbs up or thumbs down.')

                    def check(reaction, user):
                        e = str(reaction.emoji)
                        return e.startswith(('\N{THUMBS UP SIGN}', '\N{THUMBS DOWN SIGN}'))

                    res = await client.wait_for_reaction(message=msg, check=check)
                    await message.channel.send('{0.user} reacted with {0.reaction.emoji}!'.format(res))

        Parameters
        -----------
        timeout: float
            The number of seconds to wait before returning ``None``.
        user: :class:`Member` or :class:`User`
            The user the reaction must be from.
        emoji: str or :class:`Emoji` or sequence
            The emoji that we are waiting to react with.
        message: :class:`Message`
            The message that we want the reaction to be from.
        check: function
            A predicate for other complicated checks. The predicate must take
            ``(reaction, user)`` as its two parameters, which ``reaction`` being a
            :class:`Reaction` and ``user`` being either a :class:`User` or a
            :class:`Member`.

        Returns
        --------
        namedtuple
            A namedtuple with attributes ``reaction`` and ``user`` similar to :func:`on_reaction_add`.
        """

        if emoji is None:
            emoji_check = lambda r: True
        elif isinstance(emoji, (str, Emoji)):
            emoji_check = lambda r: r.emoji == emoji
        else:
            emoji_check = lambda r: r.emoji in emoji

        def predicate(reaction, reaction_user):
            result = emoji_check(reaction)

            if message is not None:
                result = result and message.id == reaction.message.id

            if user is not None:
                result = result and user.id == reaction_user.id

            if callable(check):
                # the exception thrown by check is propagated through the future.
                result = result and check(reaction, reaction_user)

            return result

        future = compat.create_future(self.loop)
        self._listeners.append((predicate, future, WaitForType.reaction))
        try:
            return (yield from asyncio.wait_for(future, timeout, loop=self.loop))
        except asyncio.TimeoutError:
            return None

    # event registration

    def event(self, coro):
        """A decorator that registers an event to listen to.

        You can find more info about the events on the :ref:`documentation below <discord-api-events>`.

        The events must be a |corourl|_, if not, :exc:`ClientException` is raised.

        Examples
        ---------

        Using the basic :meth:`event` decorator: ::

            @client.event
            @asyncio.coroutine
            def on_ready():
                print('Ready!')

        Saving characters by using the :meth:`async_event` decorator: ::

            @client.async_event
            def on_ready():
                print('Ready!')

        """

        if not asyncio.iscoroutinefunction(coro):
            raise ClientException('event registered must be a coroutine function')

        setattr(self, coro.__name__, coro)
        log.info('{0.__name__} has successfully been registered as an event'.format(coro))
        return coro

    def async_event(self, coro):
        """A shorthand decorator for ``asyncio.coroutine`` + :meth:`event`."""
        if not asyncio.iscoroutinefunction(coro):
            coro = asyncio.coroutine(coro)

        return self.event(coro)

    @asyncio.coroutine
    def change_presence(self, *, game=None, status=None, afk=False):
        """|coro|

        Changes the client's presence.

        The game parameter is a Game object (not a string) that represents
        a game being played currently.

        Parameters
        ----------
        game: Optional[:class:`Game`]
            The game being played. None if no game is being played.
        status: Optional[:class:`Status`]
            Indicates what status to change to. If None, then
            :attr:`Status.online` is used.
        afk: bool
            Indicates if you are going AFK. This allows the discord
            client to know how to handle push notifications better
            for you in case you are actually idle and not lying.

        Raises
        ------
        InvalidArgument
            If the ``game`` parameter is not :class:`Game` or None.
        """

        if status is None:
            status = 'online'
            status_enum = Status.online
        elif status is Status.offline:
            status = 'invisible'
            status_enum = Status.offline
        else:
            status_enum = status
            status = str(status)

        yield from self.ws.change_presence(game=game, status=status, afk=afk)

        for guild in self.connection.guilds:
            me = guild.me
            if me is None:
                continue

            me.game = game
            me.status = status_enum

    # Invite management

    def _fill_invite_data(self, data):
        guild = self.connection._get_guild(data['guild']['id'])
        if guild is not None:
            ch_id = data['channel']['id']
            channel = guild.get_channel(ch_id)
        else:
            guild = Object(id=data['guild']['id'])
            guild.name = data['guild']['name']
            channel = Object(id=data['channel']['id'])
            channel.name = data['channel']['name']
        data['guild'] = guild
        data['channel'] = channel

    @asyncio.coroutine
    def create_invite(self, destination, **options):
        """|coro|

        Creates an invite for the destination which could be either a
        :class:`Guild` or :class:`Channel`.

        Parameters
        ------------
        destination
            The :class:`Guild` or :class:`Channel` to create the invite to.
        max_age : int
            How long the invite should last. If it's 0 then the invite
            doesn't expire. Defaults to 0.
        max_uses : int
            How many uses the invite could be used for. If it's 0 then there
            are unlimited uses. Defaults to 0.
        temporary : bool
            Denotes that the invite grants temporary membership
            (i.e. they get kicked after they disconnect). Defaults to False.
        unique: bool
            Indicates if a unique invite URL should be created. Defaults to True.
            If this is set to False then it will return a previously created
            invite.

        Raises
        -------
        HTTPException
            Invite creation failed.

        Returns
        --------
        :class:`Invite`
            The invite that was created.
        """

        data = yield from self.http.create_invite(destination.id, **options)
        self._fill_invite_data(data)
        return Invite(**data)

    @asyncio.coroutine
    def get_invite(self, url):
        """|coro|

        Gets a :class:`Invite` from a discord.gg URL or ID.

        Note
        ------
        If the invite is for a guild you have not joined, the guild and channel
        attributes of the returned invite will be :class:`Object` with the names
        patched in.

        Parameters
        -----------
        url : str
            The discord invite ID or URL (must be a discord.gg URL).

        Raises
        -------
        NotFound
            The invite has expired or is invalid.
        HTTPException
            Getting the invite failed.

        Returns
        --------
        :class:`Invite`
            The invite from the URL/ID.
        """

        invite_id = self._resolve_invite(url)
        data = yield from self.http.get_invite(invite_id)
        self._fill_invite_data(data)
        return Invite(**data)

    @asyncio.coroutine
    def invites_from(self, guild):
        """|coro|

        Returns a list of all active instant invites from a :class:`Guild`.

        You must have proper permissions to get this information.

        Parameters
        ----------
        guild : :class:`Guild`
            The guild to get invites from.

        Raises
        -------
        Forbidden
            You do not have proper permissions to get the information.
        HTTPException
            An error occurred while fetching the information.

        Returns
        -------
        list of :class:`Invite`
            The list of invites that are currently active.
        """

        data = yield from self.http.invites_from(guild.id)
        result = []
        for invite in data:
            channel = guild.get_channel(invite['channel']['id'])
            invite['channel'] = channel
            invite['guild'] = guild
            result.append(Invite(**invite))

        return result

    @asyncio.coroutine
    def accept_invite(self, invite):
        """|coro|

        Accepts an :class:`Invite`, URL or ID to an invite.

        The URL must be a discord.gg URL. e.g. "http://discord.gg/codehere".
        An ID for the invite is just the "codehere" portion of the invite URL.

        Parameters
        -----------
        invite
            The :class:`Invite` or URL to an invite to accept.

        Raises
        -------
        HTTPException
            Accepting the invite failed.
        NotFound
            The invite is invalid or expired.
        Forbidden
            You are a bot user and cannot use this endpoint.
        """

        invite_id = self._resolve_invite(invite)
        yield from self.http.accept_invite(invite_id)

    @asyncio.coroutine
    def delete_invite(self, invite):
        """|coro|

        Revokes an :class:`Invite`, URL, or ID to an invite.

        The ``invite`` parameter follows the same rules as
        :meth:`accept_invite`.

        Parameters
        ----------
        invite
            The invite to revoke.

        Raises
        -------
        Forbidden
            You do not have permissions to revoke invites.
        NotFound
            The invite is invalid or expired.
        HTTPException
            Revoking the invite failed.
        """

        invite_id = self._resolve_invite(invite)
        yield from self.http.delete_invite(invite_id)

    # Miscellaneous stuff

    @asyncio.coroutine
    def application_info(self):
        """|coro|

        Retrieve's the bot's application information.

        Returns
        --------
        :class:`AppInfo`
            A namedtuple representing the application info.

        Raises
        -------
        HTTPException
            Retrieving the information failed somehow.
        """
        data = yield from self.http.application_info()
        return AppInfo(id=data['id'], name=data['name'],
                       description=data['description'], icon=data['icon'],
                       owner=User(state=self.connection, data=data['owner']))

    @asyncio.coroutine
    def get_user_info(self, user_id):
        """|coro|

        Retrieves a :class:`User` based on their ID. This can only
        be used by bot accounts. You do not have to share any guilds
        with the user to get this information, however many operations
        do require that you do.

        Parameters
        -----------
        user_id: str
            The user's ID to fetch from.

        Returns
        --------
        :class:`User`
            The user you requested.

        Raises
        -------
        NotFound
            A user with this ID does not exist.
        HTTPException
            Fetching the user failed.
        """
        data = yield from self.http.get_user_info(user_id)
        return User(state=self.connection, data=data)
