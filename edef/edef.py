"""edef.py - A python module to interact with the Event Definition system.
Author: Matt Gibbs (mgibbs@slac.stanford.edu)
"""

import sys
import epics
import os
import time
from functools import partial
from contextlib import contextmanager

NUM_MASK_BITS = 140

def get_system():
    """Gets the accelerator you are currently running on (LCLS, FACET, LCLS2, NLCTA, etc).
    
    Args:
        None.
    Returns:
        A tuple (sys, accelerator) containing the system string ('SYS0' for LCLS, for example), 
        and the accelerator string ('LCLS' for LCLS, for example.)
    """
    data_area = os.getenv('MATLABDATAFILES')
    if 'lcls' in data_area:
        sys = 'SYS0'
        accelerator = 'LCLS'
    if 'facet' in data_area:
        sys = 'SYS1'
        accelerator = 'FACET'
    if 'lcls2' in data_area:
        sys = 'SYS2'
        accelerator = 'LCLS2'
    if 'nlcta' in data_area:
        sys = 'SYS4'
        accelerator = 'NLCTA'
    if 'spear' in data_area:
        sys = 'SYS5'
        accelerator = 'SPEAR'
    if 'acctest' in data_area:
        sys = None
        accelerator = 'ACCTEST'
    return (sys, accelerator)

"""EventDefinition is a class that represents an event definition.
Instantiate an EventDefinition to reserve an edef.  Configure it,
then start data aquisition with the 'start' method."""
class EventDefinition(object):
    def __init__(self, name, user=None, edef_number=None, avg=1, measurements=-1, inclusion_masks=None, exclusion_masks=None, beamcode=None, avg_callback=None, measurements_callback=None, ctrl_callback=None, beamcode_callback=None):
        (self.sys, self.accelerator) = get_system()
        self.ioc_location = self.sys
        if self.accelerator == 'LCLS':
            self.ioc_location = 'IN20'
        if edef_number is None:
            self.edef_num = self.reserve_edef(name, self.sys, self.accelerator, user=user)
            print("Reserved EDEF {}".format(self.edef_num))
        else:
            self.edef_num = edef_number
        self.n_avg_pv = epics.PV("EDEF:{sys}:{num}:AVGCNT".format(sys=self.sys, num=self.edef_num))
        self.beamcode_pv = epics.PV("EDEF:{sys}:{num}:BEAMCODE".format(sys=self.sys, num=self.edef_num))
        self.n_measurements_pv = epics.PV("EDEF:{sys}:{num}:MEASCNT".format(sys=self.sys, num=self.edef_num))
        self.ctrl_pv = epics.PV("EDEF:{sys}:{num}:CTRL".format(sys=self.sys, num=self.edef_num))
        self.num_to_acquire_pv = epics.PV("EDEF:{sys}:{num}:CNTMAX".format(sys=self.sys, num=self.edef_num))
        self.num_acquired_pv = epics.PV("EDEF:{sys}:{num}:CNT".format(sys=self.sys, num=self.edef_num))
        self.bit_mask_name_cache = {}
        self.bit_mask_reverse_cache = {}
        if edef_number is None:
            #We only change the configuration of the edef if it is a brand new one.
            self.n_avg = avg
            self.n_measurements = measurements
            if inclusion_masks is not None:
                self.inclusion_masks = inclusion_masks
            if exclusion_masks is not None:
                self.exclusion_masks = exclusion_masks
            if beamcode is not None:
                self.beamcode = beamcode
        # Now that we've set initial values for PVs, we can install callbacks.
        self._avg_callback = None
        self._avg_callback_index = None
        if avg_callback is not None:
            self.avg_callback = avg_callback
        self._beamcode_callback = None
        self._beamcode_callback_index = None
        if beamcode_callback is not None:
            self.beamcode_callback = beamcode_callback
        self._measurements_callback = None
        self._measurements_callback_index = None
        if measurements_callback is not None:
            self.measurements_callback = measurements_callback
        self._ctrl_callback = None
        self._ctrl_callback_index = None
        if ctrl_callback is not None:
            self.ctrl_callback = ctrl_callback

    def reserve_edef(self, name, sys, accelerator, user=None):
        epics.caput("IOC:{iocloc}:EV01:EDEFNAME".format(iocloc=self.ioc_location), name, wait=True)
        timeout = 5.0
        time_elapsed = 0.0
        while time_elapsed < timeout:
            for num in range(1,16):
                edef_name = epics.caget("EDEF:{sys}:{num}:NAME".format(sys=sys, num=num))
                if edef_name == name:
                    if user is not None:
                        epics.caput("EDEF:{sys}:{num}:USERNAME".format(sys=sys, num=num), str(user))
                    return num
            time.sleep(0.05)
            time_elapsed += 0.05
        #If you get this far, the edef wasn't reserved.
        #Check if there just aren't any EDEFs.
        edefs_remaining_pv = "IOC:{iocloc}:EV01:EDEFAVAIL".format(iocloc=self.ioc_location)
        num_remaining = epics.caget(edefs_remaining_pv)
        if num_remaining < 1:
            raise Exception("No event definitions available.")
        else:
            raise Exception("Could not reserve an EDEF.")
    
    def is_reserved(self):
        """Checks if the edef has been reserved.

        This method checks if the edef has a valid edef_num (set during initialization).
        The most common reason an edef would not have a valid edef_num is failure to
        reserve an edef.

        Returns:
            bool: True if the edef is reserved, false otherwise.
        """
        if self.edef_num is not None and self.edef_num != 0:
            return True
        else:
            return False

    @property
    def number(self):
        """Alias for self.edef_num.
        This is for compatibility with the SC Linac BSABuffer class.
        
        Returns:
            int: Number of the event definition."""
        return self.edef_num

    @property
    def ctrl_callback(self):
        """A method to be called when the edef's ctrl state (whether or not the edef is 'on') changes.
        
        The method must take one argument: the new value of the CTRL state.
        
        To remove the callback, set ctrl_callback to None.
        """
        return self._ctrl_callback

    @ctrl_callback.setter
    def ctrl_callback(self, new_callback):
        if new_callback == self._ctrl_callback:
            return
        if self._ctrl_callback is not None:
            self.ctrl_pv.remove_callback(self._ctrl_callback_index)
        self._ctrl_callback = new_callback
        if new_callback is not None:
            full_cb = partial(self._ctrl_callback_full, new_callback)
            self._ctrl_callback_index = self.ctrl_pv.add_callback(full_cb)
    
    def _ctrl_callback_full(self, user_cb, value=None, **kw):
        user_cb(value)
    
    @property
    def n_avg(self):
        """The number of shots to average for each measurement.
        When setting n_avg, your value will be clipped to the upper and lower limits
        of the edef system.
        """
        return self.n_avg_pv.get()
    
    @n_avg.setter
    def n_avg(self, navg):
        lopr = self.n_avg_pv.get_ctrlvars()['lower_ctrl_limit']
        hopr = self.n_avg_pv.get_ctrlvars()['upper_ctrl_limit']
        self.n_avg_pv.put(min(hopr, max(lopr, navg)))
    
    @property
    def avg_callback(self):
        """A method to be called when the number of averages changes.
        This callback will fire whenever the edef's AVGCNT PV changes,
        even if it happens outside this module.

        The method must take one argument: the number of averages.
        
        To remove the callback, set avg_callback to None.
        """
        return self._avg_callback

    @avg_callback.setter
    def avg_callback(self, new_callback):
        if new_callback == self._avg_callback:
            return
        if self._avg_callback is not None:
            self.n_avg_pv.remove_callback(self._avg_callback_index)
        self._avg_callback = new_callback
        if new_callback is not None:
            full_cb = partial(self._avg_callback_full, new_callback)
            self._avg_callback_index = self.n_avg_pv.add_callback(full_cb)
    
    def _avg_callback_full(self, user_cb, value=None, **kw):
        user_cb(value)
    
    @property
    def n_measurements(self):
        """The number of measurements to take.
        A value of -1 means collect forever.
        When setting n_measurements, your value will be clipped to the upper and lower limits
        of the edef system.
        """
        return self.n_measurements_pv.get()

    @n_measurements.setter
    def n_measurements(self, measurements):
        lopr = self.n_measurements_pv.get_ctrlvars()['lower_ctrl_limit']
        hopr = self.n_measurements_pv.get_ctrlvars()['upper_ctrl_limit']
        self.n_measurements_pv.put(min(hopr, max(lopr, measurements)))
        
    @property
    def measurements_callback(self):
        """A method to be called when the number of measurements changes.
        This callback will fire whenever the edef's MEASCNT PV changes,
        even if it happens outside this module.

        The method must take one argument: the number of measurements.
        
        To remove the callback, set measurements_callback to None.
        """
        return self._measurements_callback

    @measurements_callback.setter
    def measurements_callback(self, new_callback):
        if new_callback == self._measurements_callback:
            return
        if self._measurements_callback is not None:
            self.n_measurements_pv.remove_callback(self._measurements_callback_index)
        self._measurements_callback = new_callback
        if new_callback is not None:
            full_cb = partial(self._measurements_callback_full, new_callback)
            self._measurements_callback_index = self.n_measurements_pv.add_callback(full_cb)
    
    def _measurements_callback_full(self, user_cb, value=None, **kw):
        user_cb(value)
    
    @property
    def beamcode(self):
        return self.beamcode_pv.get()
    
    @beamcode.setter
    def beamcode(self, bc):
        self.beamcode_pv.put(bc)
    
    @property
    def beamcode_callback(self):
        """A method to be called when the beamcode changes.
        This callback will fire whenever the edef's BEAMCODE PV changes,
        even if it happens outside this module.

        The method must take one argument: the beamcode number.
        
        To remove the callback, set beamcode_callback to None.
        """
        return self._beamcode_callback

    @beamcode_callback.setter
    def beamcode_callback(self, new_callback):
        if new_callback == self._beamcode_callback:
            return
        if self._beamcode_callback is not None:
            self.beamcode_pv.remove_callback(self._beamcode_callback_index)
        self._beamcode_callback = new_callback
        if new_callback is not None:
            full_cb = partial(self._beamcode_callback_full, new_callback)
            self._beamcode_callback_index = self.beamcode_pv.add_callback(full_cb)
    
    def _beamcode_callback_full(self, user_cb, value=None, **kw):
        user_cb(value)
    
    @property
    def inclusion_masks(self):
        return self.get_masks("INCLUSION")

    @inclusion_masks.setter
    def inclusion_masks(self, masks):
        self.set_masks("INCLUSION", masks)

    @property
    def exclusion_masks(self):
        return self.get_masks("EXCLUSION")

    @exclusion_masks.setter
    def exclusion_masks(self, masks):
        self.set_masks("EXCLUSION", masks)
    
    def get_masks(self, mask_type):
        if len(self.bit_mask_reverse_cache) == 0:
            self.populate_bit_mask_name_cache()
        masks = []
        bit_mask = 0
        #Combine the five modifiers into a single bit mask
        pv_template = "EDEF:{sys}:{num}:{mask_type}".format(sys=self.sys, num=self.edef_num, mask_type=mask_type)
        pv_template += "{n}"
        for modifier_num in (5, 4, 3, 2, 1):
            mod_mask = int(epics.caget(pv_template.format(n=modifier_num)))
            bit_mask = bit_mask | (mod_mask << 32*(modifier_num + 1))
        #Turn the combined bit mask into a list of modifier bit names
        for bit_num in self.bit_mask_reverse_cache:
            bit_val = bit_mask & (1 << (bit_num+32))
            if bit_val != 0:
                masks.append(self.bit_mask_reverse_cache[bit_num])
        return masks

    def set_masks(self, mask_type, masks):
        self.clear_masks(mask_type)
        if len(self.bit_mask_name_cache) == 0:
            self.populate_bit_mask_name_cache()
        bit_mask = 0
        if isinstance(masks, dict):
            for mask, val in masks.iteritems():
                bit_num = self.bit_mask_name_cache[mask]
                bit_mask = bit_mask | (val << (bit_num + 32))
        else:
            for mask in masks:
                bit_num = self.bit_mask_name_cache[mask]
                bit_mask = bit_mask | (1 << (bit_num + 32))
        #Now, break the mask up into six different modifiers!
        for modifier_num in (5, 4, 3, 2, 1):
            mod_mask = bit_mask & (0xFFFFFFFF << 32*(modifier_num + 1))
            mod_mask = mod_mask >> 32*(modifier_num + 1)
            pv_template = "EDEF:{sys}:{num}:{mask_type}".format(sys=self.sys, num=self.edef_num, mask_type=mask_type)
            pv_template += "{n}"
            epics.caput(pv_template.format(n=modifier_num), mod_mask, wait=True)
            time.sleep(0.05)

    def clear_masks(self, mask_type):
        pvs = ["EDEF:{sys}:{num}:{mask_type}{n}".format(sys=self.sys, num=self.edef_num, mask_type=mask_type, n=num) for num in range(1, 6)]
        for pv in pvs:
            epics.caput(pv, 0, wait=True)

    def populate_bit_mask_name_cache(self):
        bit_name_pvs = ["PNBN:{sys}:{n}:NAME".format(sys=self.sys, n=n) for n in range(1, NUM_MASK_BITS + 1)]
        bit_pos_pvs = ["PNBN:{sys}:{n}:BITP".format(sys=self.sys, n=n) for n in range(1, NUM_MASK_BITS + 1)]
        bit_names = epics.caget_many(bit_name_pvs)
        bit_positions = epics.caget_many(bit_pos_pvs)
        self.bit_mask_name_cache = {bit_name: bit_position for bit_name, bit_position in zip(bit_names, bit_positions)}
        self.bit_mask_reverse_cache = {bit_position: bit_name for bit_name, bit_position in zip(bit_names, bit_positions)}

    def start(self, callback=None):
        """Starts data acquisition. 
                This is equivalent to clicking the 'On' button on the edef's EDM panel.
        Raises an exception if the edef was not properly reserved.
        Returns:
            bool: True if successful, False otherwise.  
        """ 
        if not self.is_reserved():
            raise Exception("EDEF was not reserved, cannot acquire data.")
            return False
        if callback is not None:
            num_to_acquire = self.num_to_acquire_pv.get()
            full_done_cb = partial(self._done_callback, num_to_acquire, callback)
            self.num_acquired_pv.add_callback(full_done_cb)
        self.ctrl_pv.put(1)
        return True

    def stop(self):
        """Starts data acquisition. 
                This is equivalent to clicking the 'On' button on the edef's EDM panel.
        Raises an exception if the edef was not properly reserved.
        Returns:
            bool: True if successful, False otherwise.  
        """ 
        if not self.is_reserved():
            raise Exception("EDEF was not reserved, cannot stop acquisition.")
            return False
        self.ctrl_pv.put(0)
        return True

    def _done_callback(self, num_to_acquire, user_cb, value=None, cb_info=None, **kws):
        if value != num_to_acquire:
            return
        else:
            user_cb()
            cb_info[1].remove_callback(cb_info[0])  

    def wait_for_complete(self):
        while not self.is_acquisition_complete():
            time.sleep(0.05)

    def is_acquisition_complete(self):
        """Checks if the edef is done collecting data.
        Looks to see if the edef's ctrl PV is in the 'off' state.  If the PV is 'off',
        the method assumes data collection was successful.
        Raises an exception if the edef was not properly reserved.
        Returns:
            bool: True if acquisition is complete, False otherwise.
        """
        if not self.is_reserved():
            raise Exception("EDEF was not reserved, could not acquire data.")
        num_to_acquire = self.num_to_acquire_pv.get()
        num_acquired = self.num_acquired_pv.get()
        return num_acquired == num_to_acquire

    def buffer_pv(self, pv, suffix='HST'):
        return "{pv}{suffix}{num}".format(pv=pv, suffix=suffix, num=self.edef_num)

    def get_buffer(self, pv, suffix='HST'):
        string_types = (str)
        if sys.version_info[0] == 2:
            string_types = (str, unicode)
        if isinstance(pv, string_types):
            buff = epics.caget(self.buffer_pv(pv=pv, suffix=suffix))
            if self.n_measurements > 0:
                #If this isn't a rolling buffer, trim it to only include the collected data.
                buff = buff[0:self.n_measurements]
            return buff
        else:
            pv_list = [self.buffer_pv(pv=a_pv, suffix=suffix) for a_pv in pv]
            suffix_length = len(suffix + str(self.edef_num))
            buff_list = epics.caget_many(pv_list)
            buff_dict = dict(zip(pv_list, buff_list))
            if self.n_measurements > 0:
                buff_dict = {a_pv[:-suffix_length]: buff_dict[a_pv][0:self.n_measurements] for a_pv in buff_dict}
            else:
                buff_dict = {a_pv[:-suffix_length]: buff_dict[a_pv] for a_pv in buff_dict}
            return buff_dict

    def get_data_buffer(self, pv):
        """Gets the collected data for an edef measurement (or the current value of the
        buffer if n_measurements == -1).
        
        Args:
            pv (str): A BSA-capable PV (for example, "GDET:FEE:241:ENRC").  All BSA
                  system suffixes, like "HSTBR" should be left off.
        Returns:
            numpy.ndarray: An array containing the collected data for the pv.
        """
        return self.get_buffer(pv, suffix='HST')

    def get_rms_buffer(self, pv):
        """Gets the RMS data buffer for an edef measurement (or the current value of the
        buffer if n_measurements == -1).

        The RMS data buffer will only be populated if the edef's number of pulses to 
        average per measurement (n_avg) is greater than 1.
        
        Args:
            pv (str): A BSA-capable PV (for example, "GDET:FEE:241:ENRC").  All BSA
                  system suffixes, like "HSTBR" should be left off.
        Returns:
            numpy.ndarray: An array containing the RMS data for the pv.
        """
        return self.get_buffer(pv, suffix='RMSHST')

    def get_pulse_ids(self):
        return self.get_buffer("PATT:{sys}:1:PULSEID".format(sys=self.sys), suffix='HST')

    def get(self, pv):
        """Gets the current value of a PV using this edef.

        This is a convenience method equivalent to doing 
        epics.caget("GDET:FEE1:241:ENRC{num}") where {num} is the edef's number.
        
        Args:
            pv (str): A BSA-capable PV (for example, "GDET:FEE1:241:ENRC").  All BSA
                  system suffixes, like "HSTBR" should be left off.
        Returns:
            The latest value of the pv.
        """
        string_types = (str)
        if sys.version_info[0] == 2:
            string_types = (str, unicode)
        if isinstance(pv, string_types):
            return epics.caget("{pv}{num}".format(pv=pv, num=self.edef_num))
        else:
            pv_list = ["{a_pv}{num}".format(a_pv=a_pv, num=self.edef_num) for a_pv in pv]
            value_list = epics.caget_many(pv_list)
            values = dict(zip(pv_list, value_list))
            return {a_pv[:-len(str(self.edef_num))]: values[a_pv] for a_pv in values}

    def num_acquired(self):
        """Gets the number of pulses (measurements * averages) acquired by the edef.

        This can tell you the progress of a long measurement if used while acquisition
        is in progress, or it can tell you the total number of measurements performed if the
        acquisition is complete.

        Returns:
            int: The number of pulses acquired by the edef.
        """
        return epics.caget("EDEF:{sys}:{num}:CNT".format(sys=self.sys, num=self.edef_num))

    def num_to_acquire(self):
        """Gets the number of pulses to acquire (measurements * averages) by the edef.

        Returns:
            int: The number of pulses to acquire by the edef.
        """
        return epics.caget("EDEF:{sys}:{num}:CNTMAX".format(sys=self.sys, num=self.edef_num))
        
    def release(self):
        """Releases the edef.
        If the edef was not properly reserved, this method will raise an exception.
        """
        if not self.is_reserved():
            raise Exception("EDEF was not reserved, cannot release.")
        epics.caput("EDEF:{sys}:{num}:FREE".format(sys=self.sys, num=self.edef_num), 1)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
