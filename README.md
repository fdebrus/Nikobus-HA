# Nikobus-HA

**Work in progress** BETA

**implemented**
- Switches control
- Lights control (dimmers)

**open issues**
- When you change status of a switch / dimmer outside HA, eg wallswitch, it is not updated till next refresh 60secs (WIP)

**TO DO**
- add cover/shutter support
- add buttons support


You will need a RS232 to IP bridge for this integration to work (like HF2211 or others), as work complete with this proof of concept, I'm planning to extend to serial connectivity.

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/2451b88a-beff-46ce-85bf-f5486a69b37c)

**Install Instruction**

One you have installed the custom integration using HACS, go to the custom_repository/nikobus to edit nikobus_conf.json

Integration supports
  switch_modules_addresses
  dimmer_modules_addresses 
  roller_modules_addresses

Update each section to reflect your nikobus installation, module address can be found on your nikobus software.

Now add "Nikobus" as an integration

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/70cbd1c8-2e2b-4114-9cf3-f0d618e2ce52)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/ec3e56de-5b9e-404a-b97f-341c4c96331a)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/4c0eb84a-0187-418a-aa9e-24650214998b)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/6d154d91-ac59-4f44-b3c4-e7714005d15e)

![image](https://github.com/fdebrus/Nikobus-HA/assets/33791533/a5cbb377-9274-42e6-bee7-abe58c62ca82)





References

  https://github.com/timschuerewegen/homebridge-nikobus
  
  https://github.com/openhab/openhab-addons/tree/main/bundles/org.openhab.binding.nikobus

