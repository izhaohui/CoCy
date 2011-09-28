"""
..
   This file is part of the CoCy program.
   Copyright (C) 2011 Michael N. Lipp
   
   This program is free software: you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation, either version 3 of the License, or
   (at your option) any later version.
   
   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program.  If not, see <http://www.gnu.org/licenses/>.

.. codeauthor:: mnl
"""
from circuits.core.events import Event
from circuits.core.components import BaseComponent
from circuits.core.handlers import handler
from circuitsx.net.sockets import UDPMCastServer
import os
from circuits.io.events import Write
import platform
from socket import gethostname, gethostbyname
import time
from circuits.core.timers import Timer
from cocy.upnp.devices import UPnPDeviceQuery
from cocy.upnp import SSDP_ADDR, SSDP_PORT, SSDP_SCHEMAS
import datetime

class SSDPTranceiver(BaseComponent):
    '''The SSDP protocol server component
    '''

    channel = "ssdp"

    def __init__(self, web_server_port, **kwargs):
        '''
        Constructor
        '''
        kwargs.setdefault("channel", self.channel)
        super(SSDPTranceiver, self).__init__(**kwargs)

        # The underlying network connection, used by both the sender
        # and the receiver 
        self._server = UDPMCastServer((SSDP_ADDR, SSDP_PORT),
                                     **kwargs).register(self)
        self._server.setTTL(2)

        # Our associated SSDP message sender
        SSDPSender(web_server_port).register(self)
        
        # Our associated SSDP message receiver
        SSDPReceiver().register(self)



class SSDPSender(BaseComponent):
    '''The SSDP Protocol sender component
    '''

    channel = "ssdp"
    _template_dir = os.path.join(os.path.dirname(__file__), "templates")
    _template_cache = {}
    _message_env = {}
    _message_expiry = 1800
    _boot_id = int(time.time())
    _timers = dict()

    def __init__(self, web_server_port, channel=channel):
        '''
        Constructor
        '''
        super(SSDPSender, self).__init__(channel=channel)
        self.web_server_port = web_server_port

        # Setup the common entries in the dictionary that will be usd to
        # fill the UPnP templates.
        self._message_env['BOOTID'] = self._boot_id
        self._message_env['SERVER'] \
            = (platform.system() + '/' + platform.release()
               + " UPnP/1.1 CoCy/0.1")
        self.hostaddr = gethostbyname(gethostname())
        if self.hostaddr.startswith("127.") and not "." in gethostname():
            try:
                self.hostaddr = gethostbyname(gethostname() + ".")
            except:
                pass

    @handler("config_value", target="configuration")
    def _on_config_value(self, section, option, value):
        if not section == "upnp":
            return
        if option == "max-age":
            self._message_expiry = int(value)

    @handler("device_available", target="upnp")
    def _on_device_available(self, event, upnp_device):
        self._send_notification(upnp_device, "available", root_device=True)
        if getattr(event, 'times_sent', 0) < 3:
            self._timers[upnp_device.uuid] \
                = Timer(0.25, event, event.channel[1]).register(self)
            event.times_sent = getattr(event, 'times_sent', 0) + 1
        else:
            self._timers[upnp_device.uuid] \
                = Timer(self._message_expiry / 4,
                        event, event.channel[1]).register(self)
   
    @handler("device_unavailable", target="upnp")
    def _on_device_unavailable(self, event, upnp_device):
        if self._timers.has_key(upnp_device.uuid):
            self._timers[upnp_device.uuid].unregister()
            del self._timers[upnp_device.uuid]
        self._send_notification(upnp_device, "unavailable", root_device=True)
   
    @handler("upnp_device_match", target="upnp")
    def _on_device_match(self, upnp_device, inquirer):
        self._send_notification(upnp_device, "response", root_device=True)
        pass
        
    def _send_notification(self, upnp_device, type,
                           root_device = False, embedded_device = False):
        self._message_env['CACHE-CONTROL'] = self._message_expiry
        self._message_env['CONFIGID'] = upnp_device.config_id
        self._message_env['DATE'] \
            = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        self._message_env['LOCATION'] = "http://" + self.hostaddr + ":" \
            + str(self.web_server_port) + "/" + upnp_device.uuid \
            + "/description.xml"
        template = "notify-%s" % type
        # There is an extra announcement for root devices
        if root_device:
            self._message_env['NT'] = 'upnp:rootdevice'
            self._message_env['USN'] \
                = 'uuid:' + upnp_device.uuid + '::upnp:rootdevice'
            self._send_template(template, self._message_env)
        # Ordinary device announcement
        if root_device or embedded_device:
            self._message_env['NT'] = 'uuid:' + upnp_device.uuid
            self._message_env['USN'] = 'uuid:' + upnp_device.uuid
            self._send_template(template, self._message_env)
        # Common announcement
        self._message_env['NT'] \
            = SSDP_SCHEMAS + ":device:" + upnp_device.type_ver
        self._message_env['USN'] = 'uuid:' + upnp_device.uuid \
            + "::" + self._message_env['NT']
        self._send_template(template, self._message_env)
        
    def _send_template(self, templateName, data):
        template = self._get_template(templateName)
        message = template % data
        headers = ""
        for line in message.splitlines():
            headers = headers + line + "\r\n"
        headers = headers + "\r\n"
        self.fireEvent(Write((SSDP_ADDR, SSDP_PORT), headers))
                    
    def _get_template(self, name):
        if self._template_cache.has_key(name):
            return self._template_cache[name]
        file = open(os.path.join(self._template_dir, name))
        template = file.read()
        self._template_cache[name] = template
        return template


class SSDPReceiver(BaseComponent):

    channel = "ssdp"

    def __init__(self, channel = channel):
        super(SSDPReceiver, self).__init__(channel=channel)

    @handler("read")
    def _on_read(self, address, data):
        lines = data.splitlines()
        if lines[0].startswith("M-SEARCH "):
            search_target = None
            for line in lines[1:len(lines)-1]:
                if not line.startswith("ST:"):
                    continue
                search_target = line.split(':', 1)[1].strip()
                f = lambda dev: True
                # TODO: add criteria
                if search_target == "upnp:rootdevice":
                    f = lambda dev: dev.root_device
                self.fire(UPnPDeviceQuery(f, address), target="upnp")                        
                return
