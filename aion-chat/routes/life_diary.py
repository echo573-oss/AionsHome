"""生活日记的查询与手动触发接口。"""
from fastapi import APIRouter, HTTPException

import life_diary

router = APIRouter(prefix='/api/life-diary', tags=['life-diary'])


@router.get('')
async def read_diary(date: str = ''):
    return await life_diary.snapshot(date=date)


@router.post('/run')
async def run_now():
    await life_diary.ensure_schema()
    if not life_diary.enabled():
        raise HTTPException(409, '请先开启生活日记')
    ran = await life_diary.maybe_run_batch(force=True)
    return {'ok': ran, **(await life_diary.snapshot())}
