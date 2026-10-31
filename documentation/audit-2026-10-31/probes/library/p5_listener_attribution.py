"""0.56.3 _attribute_state_answer: once our ack is seen, EVERY matching
$1C for the module is filed under our group until the command layer
clears _awaiting_response. If the Feedback Module's own query for the
other group and its answer arrive after our answer but before the
command task runs (same serial read chunk, or the feedback callback not
yielding), the FM's group-2 state is filed as group 1.
"""
import asyncio
from nikobus_connect.listener import NikobusEventListener
from nikobus_connect.protocol import calc_crc1, calc_crc2, int_to_hex

def state_frame(addr, state):
    addr_le = addr[2:] + addr[:2]
    payload = addr_le + "00" + state
    body = "$1C" + payload + int_to_hex(calc_crc1(payload), 4)
    return body + int_to_hex(calc_crc2(body), 2)

async def main():
    calls = []
    class Conn: is_connected = True; device_answered = True
    lst = NikobusEventListener(Conn(), event_callback=lambda m: None,
                               feedback_callback=lambda g, m: calls.append((g, m[9:21])),
                               has_feedback_module=True)
    addr = "C9A5"
    ours, fm = state_frame(addr, "010101000000"), state_frame(addr, "FFFF00000000")
    assert lst.validate_crc(ours) and lst.validate_crc(fm)
    # what _wait_for_ack_and_answer sets before sending $1012 for group 1
    lst._awaiting_response = True
    lst._awaited_answer = "$1C" + addr[2:] + addr[:2]
    lst.set_pending_query_group(addr, 1)
    # one read chunk: our ack, our answer, FM query for group 2, its answer
    for frame in ("$0512", ours, "$1017A5C9", fm):
        await lst._dispatch_message(frame)
    print("feedback callback (group, state):", calls)
    print("expected: [(1, '010101000000'), (2, 'FFFF00000000')]")

asyncio.run(main())
