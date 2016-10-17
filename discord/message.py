# -*- coding: utf-8 -*-

"""
The MIT License (MIT)

Copyright (c) 2015-2016 Rapptz

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

import asyncio
import re

from .user import User
from .reaction import Reaction
from . import utils, abc
from .object import Object
from .calls import CallMessage
from .enums import MessageType, try_enum

class Message:
    """Represents a message from Discord.

    There should be no need to create one of these manually.

    Attributes
    -----------
    edited_timestamp: Optional[datetime.datetime]
        A naive UTC datetime object containing the edited time of the message.
    tts: bool
        Specifies if the message was done with text-to-speech.
    type: :class:`MessageType`
        The type of message. In most cases this should not be checked, but it is helpful
        in cases where it might be a system message for :attr:`system_content`.
    author
        A :class:`Member` that sent the message. If :attr:`channel` is a
        private channel, then it is a :class:`User` instead.
    content: str
        The actual contents of the message.
    nonce
        The value used by the discord guild and the client to verify that the message is successfully sent.
        This is typically non-important.
    embeds: list
        A list of embedded objects. The elements are objects that meet oEmbed's specification_.

        .. _specification: http://oembed.com/
    channel
        The :class:`Channel` that the message was sent from.
        Could be a :class:`PrivateChannel` if it's a private message.
        In :issue:`very rare cases <21>` this could be a :class:`Object` instead.

        For the sake of convenience, this :class:`Object` instance has an attribute ``is_private`` set to ``True``.
    guild: Optional[:class:`Guild`]
        The guild that the message belongs to. If not applicable (i.e. a PM) then it's None instead.
    call: Optional[:class:`CallMessage`]
        The call that the message refers to. This is only applicable to messages of type
        :attr:`MessageType.call`.
    mention_everyone: bool
        Specifies if the message mentions everyone.

        .. note::

            This does not check if the ``@everyone`` text is in the message itself.
            Rather this boolean indicates if the ``@everyone`` text is in the message
            **and** it did end up mentioning everyone.

    mentions: list
        A list of :class:`Member` that were mentioned. If the message is in a private message
        then the list will be of :class:`User` instead. For messages that are not of type
        :attr:`MessageType.default`\, this array can be used to aid in system messages.
        For more information, see :attr:`system_content`.

        .. warning::

            The order of the mentions list is not in any particular order so you should
            not rely on it. This is a discord limitation, not one with the library.

    channel_mentions: list
        A list of :class:`Channel` that were mentioned. If the message is in a private message
        then the list is always empty.
    role_mentions: list
        A list of :class:`Role` that were mentioned. If the message is in a private message
        then the list is always empty.
    id: int
        The message ID.
    webhook_id: Optional[int]
        If this message was sent by a webhook, then this is the webhook ID's that sent this
        message.
    attachments: list
        A list of attachments given to a message.
    pinned: bool
        Specifies if the message is currently pinned.
    reactions : List[:class:`Reaction`]
        Reactions to a message. Reactions can be either custom emoji or standard unicode emoji.
    """

    __slots__ = ( 'edited_timestamp', 'tts', 'content', 'channel', 'webhook_id',
                  'mention_everyone', 'embeds', 'id', 'mentions', 'author',
                  '_cs_channel_mentions', 'guild', '_cs_raw_mentions', 'attachments',
                  '_cs_clean_content', '_cs_raw_channel_mentions', 'nonce', 'pinned',
                  'role_mentions', '_cs_raw_role_mentions', 'type', 'call',
                  '_cs_system_content', '_state', 'reactions' )

    def __init__(self, *, state, channel, data):
        self._state = state
        self.reactions = kwargs.pop('reactions')
        for reaction in self.reactions:
            reaction.message = self
        self._update(channel, data)

    def _try_patch(self, data, key, transform):
        try:
            value = data[key]
        except KeyError:
            pass
        else:
            setattr(self, key, transform(value))

    def _update(self, channel, data):
        self.channel = channel
        for handler in ('mentions', 'mention_roles', 'call'):
            try:
                getattr(self, '_handle_%s' % handler)(data[handler])
            except KeyError:
                continue

        self._try_patch(data, 'edited_timestamp', utils.parse_time)
        self._try_patch(data, 'author', self._state.try_insert_user)
        self._try_patch(data, 'pinned', bool)
        self._try_patch(data, 'mention_everyone', bool)
        self._try_patch(data, 'tts', bool)
        self._try_patch(data, 'content', str)
        self._try_patch(data, 'attachments', lambda x: x)
        self._try_patch(data, 'embeds', lambda x: x)
        self._try_patch(data, 'nonce', lambda x: x)

        # clear the cached properties
        cached = filter(lambda attr: attr.startswith('_cs_'), self.__slots__)
        for attr in cached:
            try:
                delattr(self, attr)
            except AttributeError:
                pass

    def _handle_mentions(self, mentions):
        self.mentions = []
        if self.guild is None:
            self.mentions = [self._state.try_insert_user(m) for m in mentions]
            return

        for mention in mentions:
            id_search = int(mention['id'])
            member = self.guild.get_member(id_search)
            if member is not None:
                self.mentions.append(member)

    def _handle_mention_roles(self, role_mentions):
        self.role_mentions = []
        if self.guild is not None:
            for role_id in role_mentions:
                role = utils.get(self.guild.roles, id=role_id)
                if role is not None:
                    self.role_mentions.append(role)

    def _handle_call(self, call):
        if call is None or self.type is not MessageType.call:
            self.call = None
            return

        # we get the participant source from the mentions array or
        # the author

        participants = []
        for uid in map(int, call.get('participants', [])):
            if uid == self.author.id:
                participants.append(self.author)
            else:
                user = utils.find(lambda u: u.id == uid, self.mentions)
                if user is not None:
                    participants.append(user)

        call['participants'] = participants
        self.call = CallMessage(message=self, **call)

    @utils.cached_slot_property('_cs_raw_mentions')
    def raw_mentions(self):
        """A property that returns an array of user IDs matched with
        the syntax of <@user_id> in the message content.

        This allows you receive the user IDs of mentioned users
        even in a private message context.
        """
        return [int(x) for x in re.findall(r'<@!?([0-9]+)>', self.content)]

    @utils.cached_slot_property('_cs_raw_channel_mentions')
    def raw_channel_mentions(self):
        """A property that returns an array of channel IDs matched with
        the syntax of <#channel_id> in the message content.
        """
        return [int(x) for x in re.findall(r'<#([0-9]+)>', self.content)]

    @utils.cached_slot_property('_cs_raw_role_mentions')
    def raw_role_mentions(self):
        """A property that returns an array of role IDs matched with
        the syntax of <@&role_id> in the message content.
        """
        return [int(x) for x in re.findall(r'<@&([0-9]+)>', self.content)]

    @utils.cached_slot_property('_cs_channel_mentions')
    def channel_mentions(self):
        if self.guild is None:
            return []
        it = filter(None, map(lambda m: self.guild.get_channel(m), self.raw_channel_mentions))
        return utils._unique(it)

    @utils.cached_slot_property('_cs_clean_content')
    def clean_content(self):
        """A property that returns the content in a "cleaned up"
        manner. This basically means that mentions are transformed
        into the way the client shows it. e.g. ``<#id>`` will transform
        into ``#name``.

        This will also transform @everyone and @here mentions into
        non-mentions.
        """

        transformations = {
            re.escape('<#{0.id}>'.format(channel)): '#' + channel.name
            for channel in self.channel_mentions
        }

        mention_transforms = {
            re.escape('<@{0.id}>'.format(member)): '@' + member.display_name
            for member in self.mentions
        }

        # add the <@!user_id> cases as well..
        second_mention_transforms = {
            re.escape('<@!{0.id}>'.format(member)): '@' + member.display_name
            for member in self.mentions
        }

        transformations.update(mention_transforms)
        transformations.update(second_mention_transforms)

        if self.guild is not None:
            role_transforms = {
                re.escape('<@&{0.id}>'.format(role)): '@' + role.name
                for role in self.role_mentions
            }
            transformations.update(role_transforms)

        def repl(obj):
            return transformations.get(re.escape(obj.group(0)), '')

        pattern = re.compile('|'.join(transformations.keys()))
        result = pattern.sub(repl, self.content)

        transformations = {
            '@everyone': '@\u200beveryone',
            '@here': '@\u200bhere'
        }

        def repl2(obj):
            return transformations.get(obj.group(0), '')

        pattern = re.compile('|'.join(transformations.keys()))
        return pattern.sub(repl2, result)

    def _handle_upgrades(self, channel_id):
        self.guild = None
        if isinstance(self.channel, Object):
            return

        if self.channel is None:
            if channel_id is not None:
                self.channel = Object(id=channel_id)
                self.channel.is_private = True
            return

        if isinstance(self.channel, abc.GuildChannel):
            self.guild = self.channel.guild
            found = self.guild.get_member(self.author.id)
            if found is not None:
                self.author = found

    @property
    def created_at(self):
        """Returns the message's creation time in UTC."""
        return utils.snowflake_time(self.id)

    @utils.cached_slot_property('_cs_system_content')
    def system_content(self):
        """A property that returns the content that is rendered
        regardless of the :attr:`Message.type`.

        In the case of :attr:`MessageType.default`\, this just returns the
        regular :attr:`Message.content`. Otherwise this returns an English
        message denoting the contents of the system message.
        """

        if self.type is MessageType.default:
            return self.content

        if self.type is MessageType.pins_add:
            return '{0.name} pinned a message to this channel.'.format(self.author)

        if self.type is MessageType.recipient_add:
            return '{0.name} added {1.name} to the group.'.format(self.author, self.mentions[0])

        if self.type is MessageType.recipient_remove:
            return '{0.name} removed {1.name} from the group.'.format(self.author, self.mentions[0])

        if self.type is MessageType.channel_name_change:
            return '{0.author.name} changed the channel name: {0.content}'.format(self)

        if self.type is MessageType.channel_icon_change:
            return '{0.author.name} changed the channel icon.'.format(self)

        if self.type is MessageType.call:
            # we're at the call message type now, which is a bit more complicated.
            # we can make the assumption that Message.channel is a PrivateChannel
            # with the type ChannelType.group or ChannelType.private
            call_ended = self.call.ended_timestamp is not None

            if self.channel.me in self.call.participants:
                return '{0.author.name} started a call.'.format(self)
            elif call_ended:
                return 'You missed a call from {0.author.name}'.format(self)
            else:
                return '{0.author.name} started a call \N{EM DASH} Join the call.'.format(self)

    @asyncio.coroutine
    def delete(self):
        """|coro|

        Deletes the message.

        Your own messages could be deleted without any proper permissions. However to
        delete other people's messages, you need the :attr:`Permissions.manage_messages`
        permission.

        Raises
        ------
        Forbidden
            You do not have proper permissions to delete the message.
        HTTPException
            Deleting the message failed.
        """
        yield from self._state.http.delete_message(self.channel.id, self.id, getattr(self.guild, 'id', None))

    @asyncio.coroutine
    def edit(self, *, content: str):
        """|coro|

        Edits the message.

        The content must be able to be transformed into a string via ``str(content)``.

        Parameters
        -----------
        content: str
            The new content to replace the message with.

        Raises
        -------
        HTTPException
            Editing the message failed.
        """

        guild_id = getattr(self.guild, 'id', None)
        data = yield from self._state.http.edit_message(self.id, self.channel.id, str(content), guild_id=guild_id)
        self._update(channel=self.channel, data=data)

    @asyncio.coroutine
    def pin(self):
        """|coro|

        Pins the message. You must have :attr:`Permissions.manage_messages`
        permissions to do this in a non-private channel context.

        Raises
        -------
        Forbidden
            You do not have permissions to pin the message.
        NotFound
            The message or channel was not found or deleted.
        HTTPException
            Pinning the message failed, probably due to the channel
            having more than 50 pinned messages.
        """

        yield from self._state.http.pin_message(self.channel.id, self.id)
        self.pinned = True

    @asyncio.coroutine
    def unpin(self):
        """|coro|

        Unpins the message. You must have :attr:`Permissions.manage_messages`
        permissions to do this in a non-private channel context.

        Raises
        -------
        Forbidden
            You do not have permissions to unpin the message.
        NotFound
            The message or channel was not found or deleted.
        HTTPException
            Unpinning the message failed.
        """

        yield from self._state.http.unpin_message(self.channel.id, self.id)
        self.pinned = False
