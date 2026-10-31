"""GET dedup: the first caller times out, its queued item is then skipped
although a second (deduplicated) caller is still waiting on it.

_process_commands skips an item whose own future is cancelled without
looking at the other waiters in _pending_get_futures (that check is only
applied to the 'already answered' branch). B is left without an exchange
and times out although the queue reached its read in time.
"""
import asyncio
import nikobus_connect.command as cmd

cmd.COMMAND_ACK_WAIT_TIMEOUT = 1.0
cmd.COMMAND_EXECUTION_DELAY = 0.0

class Listener:
    response_queue = asyncio.Queue
    def set_pending_query_group(self, *a): pass

async def main():
    h = cmd.NikobusCommandHandler(connection=None, listener=Listener())
    sent = []
    async def fake_answer(command, address, attempts=3):
        sent.append(command)
        if address == "BLOCK":
            await asyncio.sleep(1.2)
        return "AABBCCDDEEFF"
    h._send_command_get_answer = fake_answer
    await h.start()
    await h.queue_command("$1012BLOCKER", "BLOCK", future=asyncio.get_running_loop().create_future())
    async def caller(tag, delay):
        await asyncio.sleep(delay)
        t0 = asyncio.get_running_loop().time()
        try:
            r = await h.get_output_state("C9A5", 1)
            print(f"{tag}: answered {r} after {asyncio.get_running_loop().time()-t0:.2f}s")
        except (TimeoutError, asyncio.CancelledError) as e:
            print(f"{tag}: {type(e).__name__} after {asyncio.get_running_loop().time()-t0:.2f}s")
    await asyncio.gather(caller("A", 0.0), caller("B", 0.5))
    print("frames sent for C9A5:", [c for c in sent if "C9A5" in c or "A5C9" in c], "all:", len(sent))
    await h.stop()

asyncio.run(main())
