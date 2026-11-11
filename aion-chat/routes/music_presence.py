"""播放器心跳：聊天卡片、点歌台和生成歌曲把正在听的歌报到这里。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from music_presence import record_heartbeat


router = APIRouter(prefix="/api/music", tags=["music-presence"])


class NowPlayingIn(BaseModel):
    track_id: str = ""
    external_id: str = ""
    title: str = ""
    artist: str = ""
    position_sec: float = 0
    duration_sec: float = 0
    playing: bool = False
    source: str = ""
    event: str = "time"
    lyric_key: str = Field(default="", description="仅用于客户端去重，服务端不拿它当歌词")


@router.post("/now-playing")
async def post_now_playing(body: NowPlayingIn):
    state = await record_heartbeat(body.model_dump())
    return {
        "ok": True,
        "playing": bool(state.get("playing")),
        "track_key": state.get("track_key") or "",
        "title": state.get("title") or "",
        "artist": state.get("artist") or "",
        "lyric": state.get("lyric") or "",
    }
