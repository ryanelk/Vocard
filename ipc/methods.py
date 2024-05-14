import function as func

from typing import (
    List,
    Dict
)

from discord import Member, VoiceChannel
from discord.ext import commands
from voicelink import Player, Track, Playlist, NodePool, decode, LoopType

class TempCtx():
    def __init__(self, author: Member, channel: VoiceChannel) -> None:
        self.author = author
        self.channel = channel
        self.guild = channel.guild

class SystemMethod:
    def __init__(self, function):
        self.function: callable = function
        self.params: List[str] = ["bot", "data"]

class PlayerMethod(SystemMethod):
    def __init__(self, function, *, auto_connect: bool = False):
        super().__init__(function)
        self.params: List[str] = ["player", "member", "data"]
        self.auto_connect: bool = auto_connect
        
class UserMethod(SystemMethod):
    def __init__(self, function):
        super().__init__(function)
        self.params: List[str] = ["member", "data"]
        
def missingPermission(user_id:int):
    payload = {"op": "errorMsg", "level": "info", "msg": "Only the DJ or admins may use this funciton!"}
    payload["user_id"] = user_id
    return payload

def error_msg(msg: str, *, user_id: int = None, guild_id: int = None, level: str = "info"):
    payload = {"op": "errorMsg", "level": level, "msg": msg}
    if user_id:
        payload["user_id"] = user_id
    if guild_id:
        payload["guild_id"] = guild_id

    return payload

async def connect_channel(member: Member, bot: commands.Bot):
    if not member.voice:
        return

    channel = member.voice.channel
    try:
        settings = await func.get_settings(channel.guild.id)
        player: Player = await channel.connect(cls=Player(bot, channel, TempCtx(member, channel), settings))
        await player.send_ws({"op": "createPlayer", "member_ids": [member.id for member in channel.members]})
        return player
    except:
        return

async def initUser(bot: commands.Bot, data: Dict) -> Dict:
    user_id = data.get("user_id")
    user = bot.get_user(user_id)
    if not user: 
        return
    
    guild_id = None
    for guild in user.mutual_guilds:
        member = guild.get_member(user.id)
        if member.voice and member.voice.channel:
            guild_id = guild.id
            
    return {
        "op": "initUser",
        "user_id": user_id,
        "guild_id": guild_id
    }
    
async def initPlayer(player: Player, member: Member, data: Dict):
    player._ipc_connection = True
    return {
        "op": "initPlayer",
        "guild_id": player.guild.id,
        "user_id": data.get("user_id"),
        "users": [{
            "user_id": member.id,
            "avatar_url": member.display_avatar.url,
            "name": member.name
        } for member in player.channel.members ],
        "tracks": [ track.track_id for track in player.queue._queue ],
        "repeat_mode": player.queue.repeat.lower(),
        "channel_name": player.channel.name,
        "current_queue_position": player.queue._position if player._current else player.queue._position + 1,
        "current_position": 0 or player.position if player.is_playing else 0,
        "is_playing": player.is_playing,
        "is_paused": player.is_paused,
        "is_dj": player.is_privileged(member, check_user_join=False),
        "autoplay": player.settings.get("autoplay", False)
    }

async def skipTo(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        if player.current and member == player.current.requester:
            pass

        elif member in player.skip_votes:
            return error_msg(player.get_msg('voted'), user_id=member.id)
        else:
            player.skip_votes.add(member)
            if len(player.skip_votes) >= (required := player.required()):
                pass
            else:
                return error_msg(player.get_msg('skipVote').format(member, len(player.skip_votes), required), guild_id=player.guild.id)

    index = data.get("index", 1)
    if index > 1:
        player.queue.skipto(index)

    if player.queue._repeat.mode == LoopType.track:
        await player.set_repeat(LoopType.off.name)
    await player.stop()

async def backTo(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        if player.current and member == player.current.requester:
            pass

        elif member in player.skip_votes:
            return error_msg(player.get_msg('voted'), user_id=member.id)
        else:
            player.skip_votes.add(member)
            if len(player.skip_votes) >= (required := player.required()):
                pass
            else:
                return error_msg(player.get_msg('backVote').format(member, len(player.skip_votes), required), guild_id=player.guild.id)
    
    index = data.get("index", 1)
    if not player.is_playing:
        player.queue.backto(index)
        await player.do_next()
    else:
        player.queue.backto(index + 1)
        await player.stop()

async def moveTrack(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        return missingPermission(member.id)

    c = player.queue._position - 1
    position = data.get("position")
    new_position = data.get("newPosition")

    moveItem = player.queue._queue[position]
    player.queue._queue.remove(moveItem)
    player.queue._queue.insert(new_position, moveItem)
    
    if position > c and new_position <= c:
        player.queue._position += 1

    elif position < c and new_position >= c:
        player.queue._position -= 1
    
    elif position == c:
        player.queue._position = new_position + 1

    return {
        "op": "moveTrack",
        "position": {
            "index": position - c,
            "track_id": moveItem.track_id
        },
        "newPosition": {
            "index": new_position - c
        },
        "guild_id": player.guild.id,
        "requester_id": member.id,
        "skip_users": [member.id]
    }

async def addTracks(player: Player, member: Member, data: Dict): 
    raw_tracks = data.get("tracks", [])
    tracks = [Track(
                track_id=track_id, 
                info=decode(track_id),
                requester=member
            ) for track_id in raw_tracks]

    await player.add_track(tracks)

    if not player.is_playing:
        await player.do_next()

async def getTracks(player: Player, member: Member, data: Dict):
    query = data.get("query", None)

    if query:
        payload = {"op": "getTracks", "user_id": member.id}
        tracks = await player.get_tracks(query, requester=member)
        if not tracks:
            return payload
        
        if isinstance(tracks, Playlist):
            tracks = [ track for track in tracks.tracks[:50] ]

        payload["tracks"] = [ track.track_id for track in tracks ]
        return payload
    
async def shuffleTrack(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):

        if member in player.shuffle_votes:
            return error_msg(player.get_msg('voted'), user_id=member.id) 
        else:
            player.shuffle_votes.add(member)
            if len(player.shuffle_votes) >= (required := player.required()):
                pass
            else:
                return error_msg(player.get_msg('shuffleVote').format(member, len(player.skip_votes), required), guild_id=player.guild.id)
    
    await player.shuffle(data.get("type", "queue"), member)

async def repeatTrack(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        return missingPermission(member.id)
    
    await player.set_repeat()

async def removeTrack(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        return missingPermission(member.id)
    
    position = data.get("position")
    verify_id = data.get("track_id")

    track = player.queue._queue[position]
    if track.track_id == verify_id:
        player.queue._queue.remove(track)

    if position < player.queue._position:
         player.queue._position -= 1

    return {
        "op": "removeTrack", 
        "positions": [position],
        "track_ids": [track.track_id],
        "current_queue_position": player.queue._position,
        "requester_id": member.id,
        "guild_id": player.guild.id
    }
        
async def updatePause(player: Player, member: Member, data: Dict):
    pause = data.get("pause", True)
    if not player.is_privileged(member):
        if pause:
            if member in player.pause_votes:
                return error_msg(player.get_msg('voted'), user_id=member.id)
            else:
                player.pause_votes.add(member)
                if len(player.pause_votes) >= (required := player.required()):
                    pass
                else:
                    return error_msg(player.get_msg('pauseVote').format(member, len(player.pause_votes), required), guild_id=player.guild.id)
        else:
            if member in player.resume_votes:
                return error_msg(player.get_msg('voted'), user_id=member.id)
            else:
                player.resume_votes.add(member)
                if len(player.resume_votes) >= (required := player.required()):
                    pass
                else:
                    return error_msg(player.get_msg('resumeVote').format(member, len(player.resume_votes), required), guild_id=player.guild.id)
    
    if pause:
        player.pause_votes.clear()
    else:
        player.resume_votes.clear()

    await player.set_pause(pause, member)

async def updatePosition(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        return missingPermission(member.id)
    
    position = data.get("position");
    await player.seek(position, member);

async def toggleAutoplay(player: Player, member: Member, data: Dict):
    if not player.is_privileged(member):
        return error_msg(player.get_msg('missingPerms_autoplay'))

    check = data.get("status", False)
    player.settings['autoplay'] = check

    if not player.is_playing:
        await player.do_next()

    return {
        "op": "toggleAutoplay",
        "status": check,
        "guild_id": player.guild.id,
        "requester_id": member.id
    }

async def closeConnection(player: Player, member: Member, data: Dict):
    player._ipc_connection = False

async def getPlaylists(member: Member, data: Dict):
    playlists: Dict = await func.get_user(member.id, "playlist")
    if not playlists:
        return

    for pId, pList in playlists.copy().items():
        if "type" in pList:
            if pList["type"] == "link":
                tracks: Playlist = await NodePool.get_node().get_tracks(pList["uri"], requester=member)
                if tracks:
                    playlists[pId]["tracks"] = [ track.track_id for track in tracks.tracks ]

            elif pList["type"] == "share":
                playlist = await func.get_user(pList["user"], "playlist")
                playlist = playlist.get(pList["referId"])
                if playlist:
                    if member.id not in playlist["perms"]["read"]:
                        await func.update_user(member.id, {"$unset": {f"playlist.{pId}": 1}})
                        del playlists[pId]
                        continue

                    if playlist['type'] == 'link':
                        tracks: Playlist = await NodePool.get_node().get_tracks(playlist["uri"], requester=member)
                        playlists[pId]["tracks"] = [ track.track_id for track in tracks.tracks ]
                    else:
                        playlists[pId]["tracks"] = playlist["tracks"]
            
    return {
        "op": "getPlaylists",
        "playlists": playlists,
        "user_id": member.id
    }

async def removePlaylist(member: Member, data: Dict): 
    pId = data.get("pId")
    isShare = data.get("isShare", False)

    if pId == 200:
        return

    if isShare:
        refer_user = data.get("refer_user")
        await func.update_user(refer_user, {"$pull": {f"playlist.{pId}.perms.read": member.id}})

    await func.update_user(member.id, {"$unset": {f'playlist.{pId}': 1}})

async def addPlaylistTrack(member: Member, data: Dict):
    track_id = data.get("track_id")
    pId = data.get("pId")
    if not track_id or not pId:
        return
    
    playlist: Dict = await func.get_user(member.id, 'playlist')
    playlist = playlist.get(pId)
    if not playlist:
        return
    
    if playlist["type"] != "playlist":
        return error_msg(func.get_lang(member.guild.id, 'playlistNotAllow'), user_id=member.id)
    
    rank, max_p, max_t = func.check_roles()
    if len(playlist["tracks"]) >= max_t:
        return error_msg(func.get_lang(member.guild.id, "playlistlimited").format(max_t), user_id=member.id)

    if track_id in playlist['tracks']:
        return error_msg(func.get_lang(member.guild.id, "playlistrepeated"), user_id=member.id)
    
    await func.update_user(member.id, {"$push": {f'playlist.{pId}.tracks': track_id}})

async def removePlaylistTrack(member: Member, data: Dict):
    track_id = data.get("track_id")
    pId = data.get("pId")
    if not track_id or not pId:
        return
    
    await func.update_user(member.id, {"$pull": {f'playlist.{pId}.tracks': track_id }})

methods: Dict[str, SystemMethod] = {
    "initUser": SystemMethod(initUser),
    "closeConnection": PlayerMethod(closeConnection),
    "initPlayer": PlayerMethod(initPlayer),
    "skipTo": PlayerMethod(skipTo),
    "backTo": PlayerMethod(backTo),
    "moveTrack": PlayerMethod(moveTrack),
    "addTracks": PlayerMethod(addTracks, auto_connect=True),
    "getTracks": PlayerMethod(getTracks, auto_connect=True),
    "shuffleTrack": PlayerMethod(shuffleTrack),
    "repeatTrack": PlayerMethod(repeatTrack),
    "removeTrack": PlayerMethod(removeTrack),
    "updatePause": PlayerMethod(updatePause),
    "updatePosition": PlayerMethod(updatePosition),
    "toggleAutoplay": PlayerMethod(toggleAutoplay),
    "getPlaylists": UserMethod(getPlaylists),
    "removePlaylist": UserMethod(removePlaylist),
    "addPlaylistTrack": UserMethod(addPlaylistTrack),
    "removePlaylistTrack": UserMethod(removePlaylistTrack)
}

async def process_methods(ipc_client, bot: commands.Bot, data: Dict) -> None:
    op: str = data.get("op", "")
    method = methods.get(op)
    if not method or not (user_id := data.get("user_id")):
        return

    try:
        env: Dict = {}
        args: List = []
        
        if guild_id := data.get("guild_id"):
            if not (guild := bot.get_guild(guild_id)):
                return
            env["guild"] = guild
                
        params = method.params
        if "member" in params:
            if not (guild := env.get("guild")) or not (member := guild.get_member(user_id)):
                return
            env["member"] = member
            
        if "player" in params:
            if not (guild := env.get("guild")) or not (player := guild.voice_client):
                if not method.auto_connect or not (member := env.get("member")):
                    return
                player = await connect_channel(member, bot)
            env["player"] = player
        
        if "bot" in params:
            env["bot"] = bot
        
        if "data" in params:
            env["data"] = data
        
        for param in params:
            args.append(env.get(param))
            
        if resp := await method.function(*args):
            await ipc_client.send(resp)

    except Exception as e:
        payload = {
            "op": "errorMsg",
            "level": "error",
            "msg": str(e),
            "user_id": user_id
        }
        await ipc_client.send(payload)