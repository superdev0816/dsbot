"""
The MIT License (MIT)

Copyright (c) 2015-present Rapptz

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

from __future__ import annotations

from typing import Optional, TYPE_CHECKING
from .asset import Asset
from .utils import parse_time, snowflake_time, _get_as_snowflake
from .object import Object
from .mixins import Hashable
from .enums import ChannelType, VerificationLevel, try_enum

__all__ = (
    'PartialInviteChannel',
    'PartialInviteGuild',
    'Invite',
)

if TYPE_CHECKING:
    from .types.invite import (
        Invite as InvitePayload,
        InviteGuild as InviteGuildPayload,
    )
    from .types.channel import (
        PartialChannel as PartialChannelPayload,
    )
    import datetime


class PartialInviteChannel:
    """Represents a "partial" invite channel.

    This model will be given when the user is not part of the
    guild the :class:`Invite` resolves to.

    .. container:: operations

        .. describe:: x == y

            Checks if two partial channels are the same.

        .. describe:: x != y

            Checks if two partial channels are not the same.

        .. describe:: hash(x)

            Return the partial channel's hash.

        .. describe:: str(x)

            Returns the partial channel's name.

    Attributes
    -----------
    name: :class:`str`
        The partial channel's name.
    id: :class:`int`
        The partial channel's ID.
    type: :class:`ChannelType`
        The partial channel's type.
    """

    __slots__ = ('id', 'name', 'type')

    def __init__(self, **kwargs):
        self.id = int(kwargs.pop('id'))
        self.name = kwargs.pop('name')
        self.type = kwargs.pop('type')

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'<PartialInviteChannel id={self.id} name={self.name} type={self.type!r}>'

    @property
    def mention(self):
        """:class:`str`: The string that allows you to mention the channel."""
        return f'<#{self.id}>'

    @property
    def created_at(self):
        """:class:`datetime.datetime`: Returns the channel's creation time in UTC."""
        return snowflake_time(self.id)


class PartialInviteGuild:
    """Represents a "partial" invite guild.

    This model will be given when the user is not part of the
    guild the :class:`Invite` resolves to.

    .. container:: operations

        .. describe:: x == y

            Checks if two partial guilds are the same.

        .. describe:: x != y

            Checks if two partial guilds are not the same.

        .. describe:: hash(x)

            Return the partial guild's hash.

        .. describe:: str(x)

            Returns the partial guild's name.

    Attributes
    -----------
    name: :class:`str`
        The partial guild's name.
    id: :class:`int`
        The partial guild's ID.
    verification_level: :class:`VerificationLevel`
        The partial guild's verification level.
    features: List[:class:`str`]
        A list of features the guild has. See :attr:`Guild.features` for more information.
    description: Optional[:class:`str`]
        The partial guild's description.
    """

    __slots__ = ('_state', 'features', '_icon', '_banner', 'id', 'name', '_splash', 'verification_level', 'description')

    def __init__(self, state, data: InviteGuildPayload, id: int):
        self._state = state
        self.id = id
        self.name = data['name']
        self.features = data.get('features', [])
        self._icon = data.get('icon')
        self._banner = data.get('banner')
        self._splash = data.get('splash')
        self.verification_level = try_enum(VerificationLevel, data.get('verification_level'))
        self.description = data.get('description')

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return (
            f'<{self.__class__.__name__} id={self.id} name={self.name!r} features={self.features} '
            f'description={self.description!r}>'
        )

    @property
    def created_at(self) -> datetime.datetime:
        """:class:`datetime.datetime`: Returns the guild's creation time in UTC."""
        return snowflake_time(self.id)

    @property
    def icon(self) -> Optional[Asset]:
        """Optional[:class:`Asset`]: Returns the guild's icon asset, if available."""
        if self._icon is None:
            return None
        return Asset._from_guild_icon(self._state, self.id, self._icon)

    @property
    def banner(self) -> Optional[Asset]:
        """Optional[:class:`Asset`]: Returns the guild's banner asset, if available."""
        if self._banner is None:
            return None
        return Asset._from_guild_image(self._state, self.id, self._banner, path='banners')

    @property
    def splash(self) -> Optional[Asset]:
        """Optional[:class:`Asset`]: Returns the guild's invite splash asset, if available."""
        if self._splash is None:
            return None
        return Asset._from_guild_image(self._state, self.id, self._splash, path='splashes')


class Invite(Hashable):
    r"""Represents a Discord :class:`Guild` or :class:`abc.GuildChannel` invite.

    Depending on the way this object was created, some of the attributes can
    have a value of ``None``.

    .. container:: operations

        .. describe:: x == y

            Checks if two invites are equal.

        .. describe:: x != y

            Checks if two invites are not equal.

        .. describe:: hash(x)

            Returns the invite hash.

        .. describe:: str(x)

            Returns the invite URL.

    The following table illustrates what methods will obtain the attributes:

    +------------------------------------+----------------------------------------------------------+
    |             Attribute              |                          Method                          |
    +====================================+==========================================================+
    | :attr:`max_age`                    | :meth:`abc.GuildChannel.invites`\, :meth:`Guild.invites` |
    +------------------------------------+----------------------------------------------------------+
    | :attr:`max_uses`                   | :meth:`abc.GuildChannel.invites`\, :meth:`Guild.invites` |
    +------------------------------------+----------------------------------------------------------+
    | :attr:`created_at`                 | :meth:`abc.GuildChannel.invites`\, :meth:`Guild.invites` |
    +------------------------------------+----------------------------------------------------------+
    | :attr:`temporary`                  | :meth:`abc.GuildChannel.invites`\, :meth:`Guild.invites` |
    +------------------------------------+----------------------------------------------------------+
    | :attr:`uses`                       | :meth:`abc.GuildChannel.invites`\, :meth:`Guild.invites` |
    +------------------------------------+----------------------------------------------------------+
    | :attr:`approximate_member_count`   | :meth:`Client.fetch_invite`                              |
    +------------------------------------+----------------------------------------------------------+
    | :attr:`approximate_presence_count` | :meth:`Client.fetch_invite`                              |
    +------------------------------------+----------------------------------------------------------+

    If it's not in the table above then it is available by all methods.

    Attributes
    -----------
    max_age: :class:`int`
        How long the before the invite expires in seconds.
        A value of ``0`` indicates that it doesn't expire.
    code: :class:`str`
        The URL fragment used for the invite.
    guild: Optional[Union[:class:`Guild`, :class:`Object`, :class:`PartialInviteGuild`]]
        The guild the invite is for. Can be ``None`` if it's from a group direct message.
    revoked: :class:`bool`
        Indicates if the invite has been revoked.
    created_at: :class:`datetime.datetime`
        An aware UTC datetime object denoting the time the invite was created.
    temporary: :class:`bool`
        Indicates that the invite grants temporary membership.
        If ``True``, members who joined via this invite will be kicked upon disconnect.
    uses: :class:`int`
        How many times the invite has been used.
    max_uses: :class:`int`
        How many times the invite can be used.
        A value of ``0`` indicates that it has unlimited uses.
    inviter: :class:`User`
        The user who created the invite.
    approximate_member_count: Optional[:class:`int`]
        The approximate number of members in the guild.
    approximate_presence_count: Optional[:class:`int`]
        The approximate number of members currently active in the guild.
        This includes idle, dnd, online, and invisible members. Offline members are excluded.
    channel: Union[:class:`abc.GuildChannel`, :class:`Object`, :class:`PartialInviteChannel`]
        The channel the invite is for.
    """

    __slots__ = (
        'max_age',
        'code',
        'guild',
        'revoked',
        'created_at',
        'uses',
        'temporary',
        'max_uses',
        'inviter',
        'channel',
        '_state',
        'approximate_member_count',
        'approximate_presence_count',
    )

    BASE = 'https://discord.gg'

    def __init__(self, *, state, data: InvitePayload):
        self._state = state
        self.max_age = data.get('max_age')
        self.code = data['code']
        self.guild = data.get('guild')
        self.revoked = data.get('revoked')
        self.created_at: Optional[datetime.datetime] = parse_time(data.get('created_at'))  # type: ignore
        self.temporary = data.get('temporary')
        self.uses = data.get('uses')
        self.max_uses = data.get('max_uses')
        self.approximate_presence_count = data.get('approximate_presence_count')
        self.approximate_member_count = data.get('approximate_member_count')

        inviter_data = data.get('inviter')
        self.inviter = None if inviter_data is None else self._state.store_user(inviter_data)
        self.channel = data.get('channel')

    @classmethod
    def from_incomplete(cls, *, state, data):
        try:
            guild_id = int(data['guild']['id'])
        except KeyError:
            # If we're here, then this is a group DM
            guild = None
        else:
            guild = state._get_guild(guild_id)
            if guild is None:
                # If it's not cached, then it has to be a partial guild
                guild_data = data['guild']
                guild = PartialInviteGuild(state, guild_data, guild_id)

        # As far as I know, invites always need a channel
        # So this should never raise.
        channel_data: PartialChannelPayload = data['channel']
        channel_id = int(channel_data['id'])
        channel_type = try_enum(ChannelType, channel_data['type'])
        channel = PartialInviteChannel(id=channel_id, name=channel_data['name'], type=channel_type)
        if guild is not None and not isinstance(guild, PartialInviteGuild):
            # Upgrade the partial data if applicable
            channel = guild.get_channel(channel_id) or channel

        data['guild'] = guild
        data['channel'] = channel
        return cls(state=state, data=data)

    @classmethod
    def from_gateway(cls, *, state, data):
        guild_id = _get_as_snowflake(data, 'guild_id')
        guild = state._get_guild(guild_id)
        channel_id = _get_as_snowflake(data, 'channel_id')
        if guild is not None:
            channel = guild.get_channel(channel_id) or Object(id=channel_id)
        else:
            guild = Object(id=guild_id)
            channel = Object(id=channel_id)

        data['guild'] = guild
        data['channel'] = channel
        return cls(state=state, data=data)

    def __str__(self) -> str:
        return self.url

    def __repr__(self) -> str:
        return (
            f'<Invite code={self.code!r} guild={self.guild!r} '
            f'online={self.approximate_presence_count} '
            f'members={self.approximate_member_count}>'
        )

    def __hash__(self) -> int:
        return hash(self.code)

    @property
    def id(self) -> str:
        """:class:`str`: Returns the proper code portion of the invite."""
        return self.code

    @property
    def url(self) -> str:
        """:class:`str`: A property that retrieves the invite URL."""
        return self.BASE + '/' + self.code

    async def delete(self, *, reason: Optional[str] = None):
        """|coro|

        Revokes the instant invite.

        You must have the :attr:`~Permissions.manage_channels` permission to do this.

        Parameters
        -----------
        reason: Optional[:class:`str`]
            The reason for deleting this invite. Shows up on the audit log.

        Raises
        -------
        Forbidden
            You do not have permissions to revoke invites.
        NotFound
            The invite is invalid or expired.
        HTTPException
            Revoking the invite failed.
        """

        await self._state.http.delete_invite(self.code, reason=reason)
