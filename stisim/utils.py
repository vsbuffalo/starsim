"""
Numerical utilities
"""

# %% Housekeeping

import warnings
import numpy as np
import sciris as sc
import stisim as ss
import numba as nb
import pandas as pd

# What functions are externally visible -- note, this gets populated in each section below
__all__ = []

# System constants
__all__ += ['INT_NAN']

INT_NAN = np.iinfo(np.int32).max  # Value to use to flag invalid content (i.e., an integer value we are treating like NaN, since NaN can't be stored in an integer array)


# %% Helper functions
__all__ += ['ndict', 'omerge', 'warn', 'unique', 'find_contacts', 'get_subclasses']


class ndict(sc.objdict):
    """
    A dictionary-like class that provides additional functionalities for handling named items.

    Args:
        name (str): The items' attribute to use as keys.
        type (type): The expected type of items.
        strict (bool): If True, only items with the specified attribute will be accepted.

    **Examples**::

        networks = ss.ndict(ss.mf(), ss.maternal())
        networks = ss.ndict([ss.mf(), ss.maternal()])
        networks = ss.ndict({'mf':ss.mf(), 'maternal':ss.maternal()})

    """

    def __init__(self, *args, name='name', type=None, strict=True, **kwargs):
        self.setattribute('_name', name)  # Since otherwise treated as keys
        self.setattribute('_type', type)
        self.setattribute('_strict', strict)
        self._initialize(*args, **kwargs)
        return
    
    def append(self, arg, key=None):
        valid = False
        if arg is None:
            return # Nothing to do
        elif hasattr(arg, self._name):
            key = key or getattr(arg, self._name)
            valid = True
        elif isinstance(arg, dict):
            if self._name in arg:
                key = key or arg[self._name]
                valid = True
            else:
                for k,v in arg.items():
                    self.append(v, key=k)
                valid = None # Skip final processing
        elif not self._strict:
            key = key or f'item{len(self)+1}'
            valid = True
        else:
            valid = False
        
        if valid is True:
            self._check_type(arg)
            self[key] = arg
        elif valid is None:
            pass # Nothing to do
        else:
            errormsg = f'Could not interpret argument {arg}: does not have expected attribute "{self._name}"'
            raise ValueError(errormsg)
            
        return
        
    def _check_type(self, arg):
        """ Check types """
        if self._type is not None:
            if not isinstance(arg, self._type):
                errormsg = f'The following item does not have the expected type {self._type}:\n{arg}'
                raise TypeError(errormsg)
        return
    
    def _initialize(self, *args, **kwargs):
        args = sc.mergelists(*args)
        for arg in args:
            self.append(arg)
        for key,arg in kwargs.items():
            self.append(arg, key=key)
        return
    
    def copy(self):
        new = self.__class__.__new__(name=self._name, type=self._type, strict=self._strict)
        new.update(self)
        return new
    
    def __add__(self, dict2):
        """ Allow c = a + b """
        new = self.copy()
        new.append(dict2)
        return new

    def __iadd__(self, dict2):
        """ Allow a += b """
        self.append(dict2)
        return self


def omerge(*args, **kwargs):
    """ Merge things into an objdict """
    return sc.objdict(sc.mergedicts(*args, **kwargs))


def warn(msg, category=None, verbose=None, die=None):
    """ Helper function to handle warnings -- not for the user """

    # Handle inputs
    warnopt = ss.options.warnings if not die else 'error'
    if category is None:
        category = RuntimeWarning
    if verbose is None:
        verbose = ss.options.verbose

    # Handle the different options
    if warnopt in ['error', 'errors']:  # Include alias since hard to remember
        raise category(msg)
    elif warnopt == 'warn':
        msg = '\n' + msg
        warnings.warn(msg, category=category, stacklevel=2)
    elif warnopt == 'print':
        if verbose:
            msg = 'Warning: ' + msg
            print(msg)
    elif warnopt == 'ignore':
        pass
    else:
        options = ['error', 'warn', 'print', 'ignore']
        errormsg = f'Could not understand "{warnopt}": should be one of {options}'
        raise ValueError(errormsg)

    return


def unique(arr):
    """
    Find the unique elements and counts in an array.
    Equivalent to np.unique(return_counts=True) but ~5x faster, and
    only works for arrays of positive integers.
    """
    counts = np.bincount(arr.ravel())
    unique = np.flatnonzero(counts)
    counts = counts[unique]
    return unique, counts


def find_contacts(p1, p2, inds):  # pragma: no cover
    """
    Variation on Network.find_contacts() that avoids sorting.

    A set is returned here rather than a sorted array so that custom tracing interventions can efficiently
    add extra people. For a version with sorting by default, see Network.find_contacts(). Indices must be
    an int64 array since this is what's returned by true() etc. functions by default.
    """
    pairing_partners = set()
    inds = set(inds)
    for i in range(len(p1)):
        if p1[i] in inds:
            pairing_partners.add(p2[i])
        if p2[i] in inds:
            pairing_partners.add(p1[i])
    return pairing_partners


def get_subclasses(cls):
    for subclass in cls.__subclasses__():
        yield from get_subclasses(subclass)
        yield subclass

# %% Seed methods

__all__ += ['set_seed']


def set_seed(seed=None):
    '''
    Reset the random seed -- complicated because of Numba, which requires special
    syntax to reset the seed. This function also resets Python's built-in random
    number generated.

    Args:
        seed (int): the random seed
    '''

    @nb.njit
    def set_seed_numba(seed):
        return np.random.seed(seed)

    def set_seed_regular(seed):
        return np.random.seed(seed)

    # Dies if a float is given
    if seed is not None:
        seed = int(seed)

    set_seed_regular(seed) # If None, reinitializes it
    if seed is None: # Numba can't accept a None seed, so use our just-reinitialized Numpy stream to generate one
        seed = np.random.randint(1e9)
    set_seed_numba(seed)

    return


# %% Simple array operations

__all__ += ['true', 'false', 'defined', 'undefined']

@nb.njit
def _true(uids, values):
    """
    Returns the UIDs for indices where the value evaluates as True
    """
    out = np.empty(len(uids), dtype=uids.dtype)
    j = 0
    for i in range(len(values)):
        out[j] = uids[i]
        if values[i]:
            j += 1
    out = out[0:j]
    return out

@nb.njit
def _false(uids, values):
    """
    Returns the UIDs for indices where the value evaluates as False
    """
    out = np.empty(len(uids), dtype=uids.dtype)
    j = 0
    for i in range(len(values)):
        out[j] = uids[i]
        if not values[i]:
            j += 1
    out = out[0:j]
    return out


def true(state):
    """
    Returns the UIDs of the values of the array that are true

    Args:
        state (State, FusedArray)

    **Example**::

        inds = ss.true(people.alive) # Returns array of UIDs of alive agents
    """
    return _true(state.uid.__array__(), state.__array__())


def false(state):
    """
    Returns the indices of the values of the array that are false.

    Args:
        state (State, FusedArray)

    **Example**::

        inds = ss.false(people.alive) # Returns array of UIDs of dead agents
    """
    return _false(state.uid.__array__(), state.__array__())


def defined(arr):
    """
    Returns the indices of the values of the array that are not-nan.

    Args:
        arr (array): any array

    **Example**::

        inds = ss.defined(np.array([1,np.nan,0,np.nan,1,0,1]))
    """
    return (~np.isnan(arr)).nonzero()[-1]


def undefined(arr):
    """
    Returns the indices of the values of the array that are not-nan.

    Args:
        arr (array): any array

    **Example**::

        inds = ss.defined(np.array([1,np.nan,0,np.nan,1,0,1]))
    """
    return np.isnan(arr).nonzero()[-1]


# %% Data cleaning and processing

__all__ += ['standardize_data']

def standardize_data(data=None, metadata=None):

    if isinstance(data, pd.DataFrame):
        if not set(metadata.data_cols.values()).issubset(data.columns):
            errormsg = 'Please ensure the columns of the data match the values in metadata.data_cols.'
            raise ValueError(errormsg)
        df = data

    elif isinstance(data, pd.Series):
        if (data.index < 120).all():  # Assume index is age bins
            df = pd.DataFrame({
                metadata.data_cols['year']: 2000,
                metadata.data_cols['age']: data.index.values,
                metadata.data_cols['value']: data.values,
            })
        elif (data.index > 1900).all():  # Assume index year
            df = pd.DataFrame({
                metadata.data_cols['year']: data.index.values,
                metadata.data_cols['age']: 0,
                metadata.data_cols['value']: data.values,
            })
        else:
            errormsg = 'Could not understand index of data series: should be age (all values less than 120) or year (all values greater than 1900).'
            raise ValueError(errormsg)

        df = pd.concat([df, df])
        df[metadata.data_cols['sex']] = np.repeat(list(metadata.sex_keys.values()), len(data))

    elif isinstance(data, dict):
        if not set(metadata.data_cols.values()).issubset(data.keys()):
            errormsg = 'Please ensure the keys of the data dict match the values in metadata.data_cols.'
        df = pd.DataFrame(data)

    elif sc.isnumber(data):
        df = data  # Just return it as-is

    else:
        errormsg = f'Data type {type(data)} not understood.'
        raise ValueError(errormsg)

    return df
