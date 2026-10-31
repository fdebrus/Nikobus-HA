import asyncio, contextlib
class C:
    _reload_task=None
    async def stop(self):
        t=self._reload_task
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        print("stop finished")
async def main():
    c=C()
    async def _reload():
        try:
            await asyncio.sleep(0)
            await c.stop()
            print("after stop in reload (setup would run)")
        except BaseException as e:
            print("reload got", type(e).__name__, e); raise
    c._reload_task=asyncio.get_running_loop().create_task(_reload())
    try: await c._reload_task
    except BaseException as e: print("outer", type(e).__name__)
asyncio.run(main())
