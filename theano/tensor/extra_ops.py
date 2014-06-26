import numpy as np
import numpy

import theano
from theano.tensor import basic
from theano import gof, scalar
tensor = basic
from theano.gradient import DisconnectedType


class SearchsortedOp(theano.Op):
    # See function searchsorted for docstring
    sorter_type = ('int8', 'int16', 'int32', 'int64',
                   'uint8', 'uint16', 'uint32', 'uint64')
    """Tuple of all compatible dtype for parameter ``sorter`` of this op."""

    def __init__(self, side='left'):
        self.side = side

    def __eq__(self, other):
        return (type(self) == type(other) and self.side == other.side)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.side)

    def make_node(self, x, v, sorter=None):
        x = basic.as_tensor_variable(x)
        v = basic.as_tensor_variable(v)

        if sorter is None:
            sorter = theano.tensor.as_tensor_variable(0)
        else:
            sorter = theano.tensor.as_index_variable(sorter)
            assert sorter.ndim == 1

        assert x.ndim == 1

        out_type = theano.tensor.tensor(dtype="int64", broadcastable=v.broadcastable)

        return theano.Apply(self, [x, v, sorter], [out_type])

    def perform(self, node, inputs, output_storage):
        x = inputs[0]
        v = inputs[1]
        sorter = inputs[2] if inputs[2].shape != () else None
        z = output_storage[0]
        z[0] = np.array(np.searchsorted(x, v, side=self.side, sorter=sorter))

    def grad(self, inputs, output_gradients):
        #No grad defined for intergers.
        x, nb, sorter = inputs

        x_grad = theano.gradient.grad_not_implemented(
            self, 0, x,
            "I'm not sure how to implement the gradient of searchargsort."
            " So I mark it as not implemented for now.")
        nb_grad = theano.gradient.grad_not_implemented(
            self, 1, nb,
            "I'm not sure how to implement the gradient of searchargsort."
            " So I mark it as not implemented for now.")
        sorter_grad = theano.gradient.grad_undefined(
            self, 2, sorter,
            "searchsorted is not defined for non-integer sorter so"
            " searchsorted(x, nb, sorter+eps) is undefined")
        return [x_grad, nb_grad, sorter_grad]

    def infer_shape(self, node, shapes):
        return [shapes[1]]

    def c_code(self, node, name, inames, onames, sub):
        x, v, sorter = inames
        z, = onames
        side = "NPY_SEARCHRIGHT" if self.side == 'right' else "NPY_SEARCHLEFT"
        fail = sub['fail']

        return """
            PyObject* sorter = NULL;
            if(PyArray_NDIM(%(sorter)s) > 0)
            {
                //printf("sorter: %%d\\n", PyArray_NDIM(%(sorter)s));
                sorter = (PyObject*)%(sorter)s;
            }

            Py_XDECREF(%(z)s);
            %(z)s = (PyArrayObject*) PyArray_SearchSorted(%(x)s, (PyObject*)%(v)s, %(side)s, sorter);
            //printf("z: %%d\\n", ((dtype_%(z)s *)PyArray_DATA(%(z)s))[0]);

            if (!%(z)s)
                %(fail)s;

        """ % locals()

    def c_code_cache_version(self):
        return (1,)

    def __str__(self):
        return self.__class__.__name__


def searchsorted(x, v, side='left', sorter=None):
    """Find indices where elements should be inserted to maintain order.

    Wraping of numpy.searchsorted. Find the indices into a sorted array
    ``x`` such that, if the corresponding elements in ``v`` were inserted
    before the indices, the order of ``x`` would be preserved.

    :param x: Input vector variable (1-D tensor).

    :param v: Tensor variable containing the values to insert into ``x``.

    :param side: {'left', 'right'}. If 'left', the index of the first suitable
        location found is given. If 'right', return the last such index. If
        there is no suitable index, return either 0 or N (where N is the length
        of ``x``).

    :param sorter: vector of integers (1-D tensor) containing indices that sort
        array a into ascending order. They are typically the result of argsort.

    .. versionadded:: 0.6.1
    """
    return SearchsortedOp(side=side)(x, v, sorter)

class CumsumOp(theano.Op):
    # See function cumsum for docstring
    def __init__(self, axis=None):
        self.axis = axis

    def __eq__(self, other):
        return (type(self) == type(other) and
                self.axis == other.axis)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.axis)

    def make_node(self, x):
        x = basic.as_tensor_variable(x)
        out_type = x.type()

        if self.axis is None:
            out_type = theano.tensor.vector(dtype=x.dtype)  # Flatten

        return theano.Apply(self, [x], [out_type])

    def perform(self, node, inputs, output_storage):
        x = inputs[0]
        z = output_storage[0]
        z[0] = np.cumsum(x, axis=self.axis)

    def grad(self, inputs, output_gradients):
        [gi] = output_gradients

        if self.axis is None:
            return [cumsum(gi[::-1])[::-1].reshape(inputs[0].shape)]

        # We need to reverse the gradients along ``self.axis``,
        #  compute cumsum, then reverse again
        reverse_slicing = [slice(None, None, None)] * gi.ndim
        reverse_slicing[self.axis] = slice(None, None, -1)
        reverse_slicing = tuple(reverse_slicing)
        return [cumsum(gi[reverse_slicing], self.axis)[reverse_slicing]]

    def infer_shape(self, node, shapes):
        if self.axis is None:
            return [(tensor.prod(shapes[0]),)]  # Flatten

        return shapes

    def c_code(self, node, name, inames, onames, sub):
        x, = inames
        z, = onames
        axis = self.axis
        fail = sub['fail']

        if self.axis is None or (self.axis == 0 and node.inputs[0].ndim == 1):
            code = """
                npy_intp shape[1] = { PyArray_SIZE(%(x)s) };
                if(!(%(z)s && PyArray_DIMS(%(z)s)[0] == shape[0]))
                {
                    Py_XDECREF(%(z)s);
                    %(z)s = (PyArrayObject*) PyArray_SimpleNew(1, shape, PyArray_TYPE((PyArrayObject*) py_%(x)s));
                }

                if (!%(z)s)
                    %(fail)s;
                {
                    PyArray_CumSum(%(x)s, NPY_MAXDIMS, PyArray_TYPE((PyArrayObject*) py_%(x)s), %(z)s);
                    Py_XDECREF(%(z)s);  // Because PyArray_CumSum returns a newly created reference on %(z)s.
                }
            """ % locals()
        else:
            code = """
                if(!(%(z)s && PyArray_CompareLists(PyArray_DIMS(%(z)s), PyArray_DIMS(%(x)s), PyArray_NDIM(%(x)s)) ))
                {
                    Py_XDECREF(%(z)s);
                    %(z)s = (PyArrayObject*) PyArray_SimpleNew(PyArray_NDIM(%(x)s), PyArray_DIMS(%(x)s), PyArray_TYPE((PyArrayObject*) py_%(x)s));
                }

                if (!%(z)s)
                    %(fail)s;
                {
                    PyArray_CumSum(%(x)s, %(axis)s, PyArray_TYPE((PyArrayObject*) py_%(x)s), %(z)s);
                    Py_XDECREF(%(z)s);  // Because PyArray_CumSum returns a newly created reference on %(z)s.
                }
            """ % locals()

        return code

    def c_code_cache_version(self):
        return (3,)

    def __str__(self):
        return "%s{%s}" % (self.__class__.__name__, self.axis)


def cumsum(x, axis=None):
    """Return the cumulative sum of the elements along a given axis.

    Wraping of numpy.cumsum.

    :param x: Input tensor variable.

    :param axis: The axis along which the cumulative sum is computed.
        The default (None) is to compute the cumsum over the flattened array.

    .. versionadded:: 0.6.1
    """
    return CumsumOp(axis=axis)(x)


class CumprodOp(theano.Op):
    # See function cumprod for docstring
    def __init__(self, axis=None):
        self.axis = axis

    def __eq__(self, other):
        return (type(self) == type(other) and
                self.axis == other.axis)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.axis)

    def make_node(self, x):
        x = basic.as_tensor_variable(x)
        out_type = x.type()

        if self.axis is None:
            out_type = theano.tensor.vector(dtype=x.dtype)  # Flatten

        return theano.Apply(self, [x], [out_type])

    def perform(self, node, inputs, output_storage):
        x = inputs[0]
        z = output_storage[0]
        z[0] = np.cumprod(x, axis=self.axis)

    def grad(self, inputs, output_gradients):
        x, = inputs
        gi, = output_gradients
        fx = cumprod(x, axis=self.axis)

        if self.axis is None:
            return [cumsum((fx * gi)[::-1])[::-1].reshape(inputs[0].shape) / x]

        # We need to reverse the gradients along ``self.axis``,
        #  compute cumsum, then reverse again
        reverse_slicing = [slice(None,None,None)] * gi.ndim
        reverse_slicing[self.axis] = slice(None,None,-1)
        reverse_slicing = tuple(reverse_slicing)
        return [cumsum((fx * gi)[reverse_slicing], self.axis)[reverse_slicing] / x]

    def infer_shape(self, node, shapes):
        if self.axis is None:
            return [(tensor.prod(shapes[0]),)]  # Flatten

        return shapes

    def c_code(self, node, name, inames, onames, sub):
        x, = inames
        z, = onames
        axis = self.axis
        fail = sub['fail']

        if self.axis is None or (self.axis == 0 and node.inputs[0].ndim == 1):
            code = """
                npy_intp shape[1] = { PyArray_SIZE(%(x)s) };
                if(!(%(z)s && PyArray_DIMS(%(z)s)[0] == shape[0]))
                {
                    Py_XDECREF(%(z)s);
                    %(z)s = (PyArrayObject*) PyArray_SimpleNew(1, shape, PyArray_TYPE((PyArrayObject*) py_%(x)s));
                }

                if (!%(z)s)
                    %(fail)s;
                {
                    PyArray_CumProd(%(x)s, NPY_MAXDIMS, PyArray_TYPE((PyArrayObject*) py_%(x)s), %(z)s);
                    Py_XDECREF(%(z)s);  // Because PyArray_CumSum returns a newly created reference on %(z)s.
                }
            """ % locals()
        else:
            code = """
                if(!(%(z)s && PyArray_CompareLists(PyArray_DIMS(%(z)s), PyArray_DIMS(%(x)s), PyArray_NDIM(%(x)s)) ))
                {
                    Py_XDECREF(%(z)s);
                    %(z)s = (PyArrayObject*) PyArray_SimpleNew(PyArray_NDIM(%(x)s), PyArray_DIMS(%(x)s), PyArray_TYPE((PyArrayObject*) py_%(x)s));
                }

                if (!%(z)s)
                    %(fail)s;
                {
                    PyArray_CumProd(%(x)s, %(axis)s, PyArray_TYPE((PyArrayObject*) py_%(x)s), %(z)s);
                    Py_XDECREF(%(z)s);  // Because PyArray_CumSum returns a newly created reference on %(z)s.
                }
            """ % locals()

        return code

    def c_code_cache_version(self):
        return (2,)

    def __str__(self):
        return "%s{%s}" % (self.__class__.__name__, self.axis)


def cumprod(x, axis=None):
    """Return the cumulative product of the elements along a given axis.

    Wraping of numpy.cumprod.

    :param x: Input tensor variable.

    :param axis: The axis along which the cumulative product is computed.
        The default (None) is to compute the cumprod over the flattened array.

    .. versionadded:: 0.6.1
    """
    return CumprodOp(axis=axis)(x)


class DiffOp(theano.Op):
    # See function diff for docstring
    def __init__(self, n=1, axis=-1):
        self.n = n
        self.axis = axis
        # numpy return a view in that case.
        # TODO, make an optimization that remove this op in this case.
        if n == 0:
            self.view_map = {0: [0]}

    def __eq__(self, other):
        return (type(self) == type(other) and
                self.n == other.n and
                self.axis == other.axis)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.n) ^ hash(self.axis)

    def make_node(self, x):
        x = basic.as_tensor_variable(x)
        return theano.Apply(self, [x], [x.type()])

    def perform(self, node, inputs, output_storage):
        x = inputs[0]
        z = output_storage[0]
        z[0] = np.diff(x, n=self.n, axis=self.axis)

    def grad(self, inputs, outputs_gradients):
        inputs = inputs[0]

        if inputs.ndim != 1:
            raise NotImplementedError("Grad is not implemented for inputs with"
                                      "number of dimension other than 1.")

        z = outputs_gradients[0]

        def _grad_helper(z):
            pre = basic.concatenate([[0.], z])
            app = basic.concatenate([z, [0.]])
            return pre - app

        for k in range(self.n):
            z = _grad_helper(z)
        return [z]

    def infer_shape(self, node, ins_shapes):
        i0_shapes = ins_shapes[0]
        out_shape = list(i0_shapes)
        out_shape[self.axis] = out_shape[self.axis] - self.n
        return [out_shape]

    def __str__(self):
        return self.__class__.__name__


def diff(x, n=1, axis=-1):
    """Calculate the n-th order discrete difference along given axis.

    The first order difference is given by out[i] = a[i + 1] - a[i]
    along the given axis, higher order differences are calculated by
    using diff recursively. Wraping of numpy.diff.

    :param x: Input tensor variable.

    :param n: The number of times values are differenced, default is 1.

    :param axis: The axis along which the difference is taken,
        default is the last axis.

    .. versionadded:: 0.6
    """
    return DiffOp(n=n, axis=axis)(x)


class BinCountOp(theano.Op):
    # See function bincount for docstring

    compatible_type = ('int8', 'int16', 'int32', 'int64',
                       'uint8', 'uint16', 'uint32', 'uint64')
    """Tuple of all compatible dtype for the parameter of this op."""

    def __init__(self, minlength=None):
        self.minlength = minlength
        if minlength is not None:
            numpy_ver = [int(n) for n in numpy.__version__.split('.')[:2]]
            if not bool(numpy_ver >= [1, 6]):
                raise NotImplementedError(
                    "BinCountOp with minlength attribute"
                    " requires NumPy 1.6 or higher.")

    def __eq__(self, other):
        return (type(self) == type(other) and
               self.minlength == other.minlength)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.minlength)

    def make_node(self, x, weights):
        x = basic.as_tensor_variable(x)

        if x.dtype not in BinCountOp.compatible_type:
            raise TypeError("Inputs dtype must be an integer.")

        # Some dtypes are not supported by numpy's implementation of bincount.
        # Until another one is available, we should fail at graph construction
        # time, not wait for execution.
        int_bitwidth = theano.gof.python_int_bitwidth()
        if int_bitwidth == 64:
            numpy_unsupported_dtypes = ('uint64',)
        if int_bitwidth == 32:
            numpy_unsupported_dtypes = ('uint32', 'int64', 'uint64')
        intp_bitwidth = theano.gof.local_bitwidth()
        if intp_bitwidth == 32:
            out_type = basic.ivector()
        elif intp_bitwidth == 64:
            out_type = basic.lvector()

        if x.dtype in numpy_unsupported_dtypes:
            raise TypeError(
                    ("Input dtypes %s are not supported by numpy.bincount, "
                    % numpy_unsupported_dtypes), x.dtype)

        if x.ndim != 1:
            raise TypeError("Inputs must be of dimension 1.")

        if weights is None:
            weights = theano.gof.Constant(theano.gof.Generic(), None)
        else:
            weights = basic.as_tensor_variable(weights)
            out_type = basic.dvector()
            if weights.ndim != 1:
                raise TypeError("Weights cannot have a number of"
                                "dimension different of 1.")

        return theano.Apply(self, [x, weights], [out_type])

    def perform(self, node, inputs, output_storage):
        x = inputs[0]
        weights = inputs[1]
        z = output_storage[0]

        if weights is not None and weights.shape != x.shape:
            raise TypeError("All inputs must have the same shape.")

        # Needed for numpy 1.4.1 compatibility
        if self.minlength:
            out = np.bincount(x, weights=weights, minlength=self.minlength)
        else:
            out = np.bincount(x, weights=weights)

        z[0] = theano._asarray(out, dtype=node.outputs[0].dtype)

    def grad(self, inputs, outputs_gradients):
        output = self(*inputs)

        if output.dtype.find('int') != -1:
            return [inp.zeros_like().astype(theano.config.floatX)
                    for inp in inputs]

        raise NotImplementedError()

    def infer_shape(self, node, ins_shapes):
        x = node.inputs[0]
        m = basic.max(x) + 1
        if self.minlength is not None:
            m = basic.maximum(m, self.minlength)
        return [[m]]

    def __str__(self):
        return self.__class__.__name__


def bincount(x, weights=None, minlength=None):
    """Count number of occurrences of each value in array of non-negative ints.

    The number of bins (of size 1) is one larger than the largest
    value in x. If minlength is specified, there will be at least
    this number of bins in the output array (though it will be longer
    if necessary, depending on the contents of x). Each bin gives the
    number of occurrences of its index value in x. If weights is
    specified the input array is weighted by it, i.e. if a value n
    is found at position i, out[n] += weight[i] instead of out[n] += 1.
    Wraping of numpy.bincount

    :param x: 1 dimension, nonnegative ints

    :param weights: array of the same shape as x with corresponding weights.
        Optional.
    :param minlength: A minimum number of bins for the output array.
        Optional.

    .. versionadded:: 0.6
    """
    return BinCountOp(minlength=minlength)(x, weights)


def squeeze(x):
    """Remove broadcastable dimensions from
    the shape of an array.

    It returns the input array, but with the
    broadcastable dimensions removed. This is
    always `x` itself or a view into `x`.

    :param x: Input data, tensor variable.

    :return: `x` without its broadcastable dimensions.

    .. versionadded:: 0.6
    """
    view = x.dimshuffle([i for i in range(x.ndim)
                         if not x.broadcastable[i]])
    return view


class RepeatOp(theano.Op):
    # See the repeat function for docstring

    def __init__(self, axis=None):
        self.axis = axis

    def __eq__(self, other):
        return (type(self) == type(other) and
                self.axis == other.axis)

    def __hash__(self):
        return hash(type(self)) ^ hash(self.axis)

    def make_node(self, x, repeats):
        x = basic.as_tensor_variable(x)
        repeats = basic.as_tensor_variable(repeats)

        if repeats.dtype not in tensor.discrete_dtypes:
            raise TypeError("repeats.dtype must be an integer.")

        # Some dtypes are not supported by numpy's implementation of repeat.
        # Until another one is available, we should fail at graph construction
        # time, not wait for execution.
        ptr_bitwidth = theano.gof.local_bitwidth()
        if ptr_bitwidth == 64:
            numpy_unsupported_dtypes = ('uint64',)
        if ptr_bitwidth == 32:
            numpy_unsupported_dtypes = ('uint32', 'int64', 'uint64')

        if repeats.dtype in numpy_unsupported_dtypes:
            raise TypeError(
                    ("dtypes %s are not supported by numpy.repeat "
                     "for the 'repeats' parameter, "
                     % str(numpy_unsupported_dtypes)), repeats.dtype)

        if self.axis is None:
            broadcastable = [False]
        else:
            try:
                const_reps = basic.get_scalar_constant_value(repeats)
            except basic.NotScalarConstantError:
                const_reps = None
            if const_reps == 1:
                broadcastable = x.broadcastable
            else:
                broadcastable = list(x.broadcastable)
                broadcastable[self.axis] = False

        out_type = theano.tensor.TensorType(x.dtype, broadcastable)

        return theano.Apply(self, [x, repeats], [out_type()])

    def perform(self, node, inputs, output_storage):
        x = inputs[0]
        repeats = inputs[1]
        z = output_storage[0]
        z[0] = np.repeat(x, repeats=repeats, axis=self.axis)

    def connection_pattern(self, node):

        return [[True], [False]]

    def grad(self, (x, repeats), (gz, )):
        if repeats.ndim == 0:
            if self.axis is None:
                axis = x.ndim
            else:
                if self.axis >= 0:
                    axis = self.axis + 1
                else:
                    axis = self.axis + x.ndim + 1

            shape = [x.shape[k] for k in range(x.ndim)]
            shape.insert(axis, repeats)

            return [gz.reshape(shape, x.ndim + 1).sum(axis=axis),
                    DisconnectedType()()]
        elif repeats.ndim == 1:
            # For this implementation, we would need to specify the length
            # of repeats in order to split gz in the right way to sum
            # the good part.
            raise NotImplementedError()
        else:
            raise ValueError()

    def infer_shape(self, node, ins_shapes):
        i0_shapes = ins_shapes[0]
        repeats = node.inputs[1]
        out_shape = list(i0_shapes)

        #uint64 shape are not supported.
        dtype = None
        if repeats.dtype in ['uint8', 'uint16', 'uint32']:
            dtype = 'int64'
        if self.axis is None:
            if repeats.ndim == 0:
                if len(i0_shapes) == 0:
                    out_shape = [repeats]
                else:
                    res = 1
                    for d in i0_shapes:
                        res = res * d
                    out_shape = (res * repeats, )
            else:
                out_shape = [theano.tensor.sum(repeats, dtype=dtype)]
        else:
            if repeats.ndim == 0:
                out_shape[self.axis] = out_shape[self.axis] * repeats
            else:
                out_shape[self.axis] = theano.tensor.sum(repeats, dtype=dtype)
        return [out_shape]

    def __str__(self):
        return self.__class__.__name__


def repeat(x, repeats, axis=None):
    """Repeat elements of an array.

    It returns an array which has the same shape as `x`, except
    along the given axis. The axis is used to speficy along which
    axis to repeat values. By default, use the flattened input
    array, and return a flat output array.

    The number of repetitions for each element is `repeat`.
    `repeats` is broadcasted to fit the length of the given `axis`.

    :param x: Input data, tensor variable.
    :param repeats: int, scalar or tensor variable.

    :param axis: int, optional.

    :see: :func:`tensor.tile <tensor.tile>`

    .. versionadded:: 0.6
    """
    return RepeatOp(axis=axis)(x, repeats)


class Bartlett(gof.Op):
    # See function bartlett for docstring
    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def make_node(self, M):
        M = tensor.as_tensor_variable(M)
        if M.ndim != 0:
            raise TypeError('%s only works on scalar input'
                            % self.__class__.__name__)
        elif (not M.dtype.startswith('int')) and \
              (not M.dtype.startswith('uint')):
        # dtype is a theano attribute here
            raise TypeError('%s only works on integer input'
                            % self.__class__.__name__)
        return gof.Apply(self, [M], [tensor.dvector()])

    def perform(self, node, inputs, out_):
        M = inputs[0]
        out, = out_
        out[0] = numpy.bartlett(M)

    def infer_shape(self, node, in_shapes):
        temp = node.inputs[0]
        M = tensor.switch(tensor.lt(temp, 0),
            tensor.cast(0, temp.dtype), temp)
        return [[M]]

    def grad(self, inputs, output_grads):
        return [None for i in inputs]
bartlett_ = Bartlett()


#I create a function only to have the doc show well.
def bartlett(M):
    """An instance of this class returns the Bartlett spectral window in the
    time-domain. The Bartlett window is very similar to a triangular window,
    except that the end points are at zero. It is often used in signal
    processing for tapering a signal, without generating too much ripple in
    the frequency domain.

    :param M: (integer scalar) Number of points in the output
        window. If zero or less, an empty vector is returned.

    :return: (vector of doubles) The triangular window, with the
        maximum value normalized to one (the value one appears only if
        the number of samples is odd), with the first and last samples
        equal to zero.

    .. versionadded:: 0.6

    """
    return bartlett_(M)


class FillDiagonal(gof.Op):
    # See function fill_diagonal for docstring
    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def __str__(self):
        return self.__class__.__name__

    def infer_shape(self, node, in_shapes):
        return [in_shapes[0]]

    def make_node(self, a, val):
        a = tensor.as_tensor_variable(a)
        val = tensor.as_tensor_variable(val)
        if a.ndim < 2:
            raise TypeError('%s: first parameter must have at least'
                            ' two dimensions' % self.__class__.__name__)
        elif val.ndim != 0:
            raise TypeError('%s: second parameter must be a scalar'
                            % self.__class__.__name__)
        val = tensor.cast(val, dtype=scalar.upcast(a.dtype, val.dtype))
        if val.dtype != a.dtype:
            raise TypeError('%s: type of second parameter must be the same as'
                          ' the first\'s' % self.__class__.__name__)
        return gof.Apply(self, [a, val], [a.type()])

    def perform(self, node, inputs, output_storage):
        a = inputs[0].copy()
        val = inputs[1]
        if a.ndim == 2:
            # numpy.fill_diagonal up to date(including 1.6.2) have a
            # bug for tall matrix.
            # For 2-d arrays, we accept rectangular ones.
            step = a.shape[1] + 1
            end = a.shape[1] * a.shape[1]
            # Write the value out into the diagonal.
            a.flat[:end:step] = val
        else:
            numpy.fill_diagonal(a, val)

        output_storage[0][0] = a

    def grad(self, inp, cost_grad):
        """
        Note: The gradient is currently implemented for matrices
        only.
        """
        a, val = inp
        grad = cost_grad[0]
        if (a.dtype.startswith('complex')):
            return [None, None]
        elif a.ndim > 2:
            raise NotImplementedError('%s: gradient is currently implemented'
                            ' for matrices only' % self.__class__.__name__)
        wr_a = fill_diagonal(grad, 0)  # valid for any number of dimensions
        # diag is only valid for matrices
        import theano.sandbox.linalg
        wr_val = theano.sandbox.linalg.ops.diag(grad).sum()
        return [wr_a, wr_val]
fill_diagonal_ = FillDiagonal()


#I create a function only to have the doc show well.
def fill_diagonal(a, val):
    """ Returns a copy of an array with all
    elements of the main diagonal set to a specified scalar value.

    :param a: Rectangular array of at least two dimensions.
    :param val: Scalar value to fill the diagonal whose type must be
        compatible with that of array 'a' (i.e. 'val' cannot be viewed
        as an upcast of 'a').

    :return: An array identical to 'a' except that its main diagonal
        is filled with scalar 'val'. (For an array 'a' with a.ndim >=
        2, the main diagonal is the list of locations a[i, i, ..., i]
        (i.e. with indices all identical).)

    Support rectangular matrix and tensor with more than 2 dimensions
    if the later have all dimensions are equals.

    .. versionadded:: 0.6
    """
    return fill_diagonal_(a, val)
