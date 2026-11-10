# Commands on the bus

Home Assistant reaches an output in one of two ways, chosen per channel by the **LED on / off key** fields under *Customize a module*.

**Switching the output directly.** With the LED fields empty, `light.turn_on`, `switch.turn_off`, a fan speed or a cover command sends a set-output frame to the module, which acknowledges it: the end state is defined whatever the output was doing before, and Home Assistant updates the entity on the spot. A set-output frame carries all six outputs of the module's group, taken from the state Home Assistant last knew, so a group whose state may be behind is read first; a light switched at the wall a moment ago therefore stays as it is instead of being switched back. The read costs about half a second before the relay clicks; the entity still shows the new state at once. It is skipped where nothing can have changed unseen: a group of a switch module whose links were all read by **2. Load Existing Installation** and none of which runs a timer (delayed off, an operating time other than zero, flashing) or comes from a PC-Link calendar channel, as long as the group was heard from within two refresh periods and no key press has reached it since. A press seen on the bus, or sent by Home Assistant, makes the next command to the groups it drives read first; a press of a key with no known link does so for every group. Dimmer and shutter groups are always read first, since a level may still be moving and a motor stops by its run time. The diagnostics download lists the groups written without a read under `written_without_a_read_first`. Requests for the same module group that arrive together — six lights of one module in one service call — are merged into one frame carrying all six bytes: one acknowledgement, one relay click. Plate LEDs do not follow: the Feedback Module (05-207), which drives them, toggles a key's LED on every key telegram it sees and keeps that state itself; a set-output never reaches it. An output switched *on* this way shows on the LED when the Feedback Module next reads that module's group, which took 12 to 30 minutes on the installation tested; an output switched *off* is not picked up by those reads: the LED stayed on through three reads over 40 minutes, and an inverted LED took hours to clear overnight (05-207, ten output modules, November 2026). Until then the LED of a key in impulse mode shows the opposite of the light.

**Pressing the key.** With a key address in the LED fields, Home Assistant *presses that key* instead: the same `#N` telegram a wall press puts on the bus, so the module applies whatever the key is linked to (on/off, impulse, timer) and the Feedback Module updates the LEDs at once — on every plate whose key drives the same output, not only this one. Use this for channels whose LED must stay in step, and for keys in **impulse mode** (M05), which toggle: a set-output command would leave the LED behind. On a relay light or switch, a key press is checked against the module: the channel is read first and the key is not pressed when the output is already where it should be (so `light.turn_off` on a light that is off leaves it off, even with a toggle key); after the press the channel is read again, and an output that did not move is set directly, with a warning in the log (the LED then lags until the Feedback Module's next poll of that module). `light.toggle` and `switch.toggle` decide from what the module reports, not from what Home Assistant shows. A dimmer channel with an LED key gets both: the channel is read, the key is pressed only when the light goes from off to on or from on to off, so its LED follows, and the brightness is then set directly, which is the only way to reach an exact level; a brightness change on a light that is already on sends no press. The entity keeps the state it was asked for while that runs. The entity keeps the state it was asked for until the press has been checked, then shows what the module reported, so a switch is one state change in the recorder, never the intermediate reading.

A press from Home Assistant — a button entity, an LED-key channel, a scene, a central function — is one write of the telegram repeated *Simulated press repeats* times back to back, which is how a held key looks on the bus and what a module needs to count it as one press. The PC-Link never relays the host's own telegram back, so Home Assistant reads the impacted modules itself about a second after the press; an automation can therefore check the state a few seconds after `button.press` and press again only if the impulse was lost. No `nikobus_button_operation` event is fired for a press Home Assistant sent.

```yaml
# Impulse key that toggles six lamps: press only while they are still off,
# verify after 3 s, one retry.
actions:
  - repeat:
      count: 2
      sequence:
        - if:
            - condition: state
              entity_id: light.verlichting_boom_1
              state: "off"
          then:
            - action: button.press
              target:
                entity_id: button.nikobus_push_button_295682
            - delay:
                seconds: 3
```
