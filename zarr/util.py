# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division
import operator
from textwrap import TextWrapper
import numbers


import numpy as np


from zarr.compat import PY2, reduce, text_type, binary_type


def normalize_shape(shape):
    """Convenience function to normalize the `shape` argument."""

    if shape is None:
        raise TypeError('shape is None')

    # handle 1D convenience form
    if isinstance(shape, numbers.Integral):
        shape = (int(shape),)

    # normalize
    shape = tuple(int(s) for s in shape)
    return shape


# code to guess chunk shape, adapted from h5py

CHUNK_BASE = 128*1024  # Multiplier by which chunks are adjusted
CHUNK_MIN = 128*1024  # Soft lower limit (128k)
CHUNK_MAX = 64*1024*1024  # Hard upper limit


def guess_chunks(shape, typesize):
    """
    Guess an appropriate chunk layout for a dataset, given its shape and
    the size of each element in bytes.  Will allocate chunks only as large
    as MAX_SIZE.  Chunks are generally close to some power-of-2 fraction of
    each axis, slightly favoring bigger values for the last index.
    Undocumented and subject to change without warning.
    """

    ndims = len(shape)
    # require chunks to have non-zero length for all dimensions
    chunks = np.maximum(np.array(shape, dtype='=f8'), 1)

    # Determine the optimal chunk size in bytes using a PyTables expression.
    # This is kept as a float.
    dset_size = np.product(chunks)*typesize
    target_size = CHUNK_BASE * (2**np.log10(dset_size/(1024.*1024)))

    if target_size > CHUNK_MAX:
        target_size = CHUNK_MAX
    elif target_size < CHUNK_MIN:
        target_size = CHUNK_MIN

    idx = 0
    while True:
        # Repeatedly loop over the axes, dividing them by 2.  Stop when:
        # 1a. We're smaller than the target chunk size, OR
        # 1b. We're within 50% of the target chunk size, AND
        # 2. The chunk is smaller than the maximum chunk size

        chunk_bytes = np.product(chunks)*typesize

        if (chunk_bytes < target_size or
                abs(chunk_bytes-target_size)/target_size < 0.5) and \
                chunk_bytes < CHUNK_MAX:
            break

        if np.product(chunks) == 1:
            break  # Element size larger than CHUNK_MAX

        chunks[idx % ndims] = np.ceil(chunks[idx % ndims] / 2.0)
        idx += 1

    return tuple(int(x) for x in chunks)


def normalize_chunks(chunks, shape, typesize):
    """Convenience function to normalize the `chunks` argument for an array
    with the given `shape`."""

    # N.B., expect shape already normalized

    # handle auto-chunking
    if chunks is None or chunks is True:
        return guess_chunks(shape, typesize)

    # handle 1D convenience form
    if isinstance(chunks, numbers.Integral):
        chunks = (int(chunks),)

    # handle bad dimensionality
    if len(chunks) > len(shape):
        raise ValueError('too many dimensions in chunks')

    # handle underspecified chunks
    if len(chunks) < len(shape):
        # assume chunks across remaining dimensions
        chunks += shape[len(chunks):]

    # handle None in chunks
    chunks = tuple(s if c is None else int(c)
                   for s, c in zip(shape, chunks))

    return chunks


# noinspection PyTypeChecker
def is_total_slice(item, shape):
    """Determine whether `item` specifies a complete slice of array with the
    given `shape`. Used to optimize __setitem__ operations on the Chunk
    class."""

    # N.B., assume shape is normalized

    if item == Ellipsis:
        return True
    if item == slice(None):
        return True
    if isinstance(item, slice):
        item = item,
    if isinstance(item, tuple):
        return all(
            (isinstance(s, slice) and
                ((s == slice(None)) or
                 ((s.stop - s.start == l) and (s.step in [1, None]))))
            for s, l in zip(item, shape)
        )
    else:
        raise TypeError('expected slice or tuple of slices, found %r' % item)


def normalize_resize_args(old_shape, *args):

    # normalize new shape argument
    if len(args) == 1:
        new_shape = args[0]
    else:
        new_shape = args
    if isinstance(new_shape, int):
        new_shape = (new_shape,)
    else:
        new_shape = tuple(new_shape)
    if len(new_shape) != len(old_shape):
        raise ValueError('new shape must have same number of dimensions')

    # handle None in new_shape
    new_shape = tuple(s if n is None else int(n)
                      for s, n in zip(old_shape, new_shape))

    return new_shape


def human_readable_size(size):
    if size < 2**10:
        return '%s' % size
    elif size < 2**20:
        return '%.1fK' % (size / float(2**10))
    elif size < 2**30:
        return '%.1fM' % (size / float(2**20))
    elif size < 2**40:
        return '%.1fG' % (size / float(2**30))
    elif size < 2**50:
        return '%.1fT' % (size / float(2**40))
    else:
        return '%.1fP' % (size / float(2**50))


def normalize_order(order):
    order = str(order).upper()
    if order not in ['C', 'F']:
        raise ValueError("order must be either 'C' or 'F', found: %r" % order)
    return order


def normalize_fill_value(fill_value, dtype):

    if fill_value is None:
        # no fill value
        pass

    elif fill_value == 0 and dtype.kind == 'V':
        # special case because 0 used as default, but cannot be used for structured arrays
        fill_value = b''

    elif dtype.kind == 'U':
        # special case unicode because of encoding issues on Windows if passed through numpy
        # https://github.com/alimanfoo/zarr/pull/172#issuecomment-343782713

        if PY2 and isinstance(fill_value, binary_type):  # pragma: py3 no cover
            # this is OK on PY2, can be written as JSON
            pass

        elif not isinstance(fill_value, text_type):
            raise ValueError('fill_value {!r} is not valid for dtype {}; must be a unicode string'
                             .format(fill_value, dtype))

    else:
        try:
            fill_value = np.array(fill_value, dtype=dtype)[()]
        except Exception as e:
            # re-raise with our own error message to be helpful
            raise ValueError('fill_value {!r} is not valid for dtype {}; nested exception: {}'
                             .format(fill_value, dtype, e))
    return fill_value


def normalize_storage_path(path):

    # handle bytes
    if not PY2 and isinstance(path, bytes):  # pragma: py2 no cover
        path = str(path, 'ascii')

    # ensure str
    if path is not None and not isinstance(path, str):
        path = str(path)

    if path:

        # convert backslash to forward slash
        path = path.replace('\\', '/')

        # ensure no leading slash
        while len(path) > 0 and path[0] == '/':
            path = path[1:]

        # ensure no trailing slash
        while len(path) > 0 and path[-1] == '/':
            path = path[:-1]

        # collapse any repeated slashes
        previous_char = None
        collapsed = ''
        for char in path:
            if char == '/' and previous_char == '/':
                pass
            else:
                collapsed += char
            previous_char = char
        path = collapsed

        # don't allow path segments with just '.' or '..'
        segments = path.split('/')
        if any([s in {'.', '..'} for s in segments]):
            raise ValueError("path containing '.' or '..' segment not allowed")

    else:
        path = ''

    return path


def buffer_size(v):
    from array import array as _stdlib_array
    if PY2 and isinstance(v, _stdlib_array):  # pragma: py3 no cover
        # special case array.array because does not support buffer
        # interface in PY2
        return v.buffer_info()[1] * v.itemsize
    else:  # pragma: py2 no cover
        v = memoryview(v)
        return reduce(operator.mul, v.shape) * v.itemsize


def info_text_report(items):
    keys = [k for k, v in items]
    max_key_len = max(len(k) for k in keys)
    report = ''
    for k, v in items:
        wrapper = TextWrapper(width=80,
                              initial_indent=k.ljust(max_key_len) + ' : ',
                              subsequent_indent=' '*max_key_len + ' : ')
        text = wrapper.fill(str(v))
        report += text + '\n'
    return report


def info_html_report(items):
    report = '<table class="zarr-info">'
    report += '<tbody>'
    for k, v in items:
        report += '<tr>' \
                  '<th style="text-align: left">%s</th>' \
                  '<td style="text-align: left">%s</td>' \
                  '</tr>' \
                  % (k, v)
    report += '</tbody>'
    report += '</table>'
    return report


class InfoReporter(object):

    def __init__(self, obj):
        self.obj = obj

    def __repr__(self):
        items = self.obj.info_items()
        return info_text_report(items)

    def _repr_html_(self):
        items = self.obj.info_items()
        return info_html_report(items)


def check_array_shape(param, array, shape):
    if not hasattr(array, 'shape'):
        raise TypeError('parameter {!r}: expected an array-like object, got {!r}'
                        .format(param, type(array)))
    if array.shape != shape:
        raise ValueError('parameter {!r}: expected array with shape {!r}, got {!r}'
                         .format(param, shape, array.shape))
