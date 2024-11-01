import os
from typing import List

import metatensor
import numpy as np
import pytest
from metatensor import Labels, TensorBlock, TensorMap

import rascaline
from rascaline.utils import PowerSpectrum, _dispatch
from rascaline.utils.clebsch_gordan import DensityCorrelations
from rascaline.utils.clebsch_gordan._coefficients import calculate_cg_coefficients
from rascaline.utils.clebsch_gordan._utils import standardize_keys


# Try to import some modules
ase = pytest.importorskip("ase")
import ase.io  # noqa: E402


try:
    import metatensor.operations

    HAS_METATENSOR_OPERATIONS = True
except ImportError:
    HAS_METATENSOR_OPERATIONS = False
try:
    import sympy  # noqa: F401

    HAS_SYMPY = True
except ImportError:
    HAS_SYMPY = False


if HAS_SYMPY:
    from .rotations import WignerDReal, transform_frame_o3, transform_frame_so3


try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

if HAS_TORCH:
    ARRAYS_BACKEND = ["numpy", "torch"]
else:
    ARRAYS_BACKEND = ["numpy"]

DATA_ROOT = os.path.join(os.path.dirname(__file__), "data")

SPHEX_HYPERS = {
    "cutoff": 2.5,  # Angstrom
    "max_radial": 3,  # Exclusive
    "max_angular": 3,  # Inclusive
    "atomic_gaussian_width": 0.2,
    "radial_basis": {"Gto": {}},
    "cutoff_function": {"ShiftedCosine": {"width": 0.5}},
    "center_atom_weight": 1.0,
}

SPHEX_HYPERS_SMALL = {
    "cutoff": 2.5,  # Angstrom
    "max_radial": 1,  # Exclusive
    "max_angular": 2,  # Inclusive
    "atomic_gaussian_width": 0.2,
    "radial_basis": {"Gto": {}},
    "cutoff_function": {"ShiftedCosine": {"width": 0.5}},
    "center_atom_weight": 1.0,
}


# ============ Helper functions ============


def h2_isolated():
    return [
        ase.Atoms(
            symbols=["H", "H"],
            positions=[
                [1.97361700, 1.73067300, 2.47063400],
                [1.97361700, 3.26932700, 2.47063400],
            ],
        )
    ]


def h2o_isolated():
    return [
        ase.Atoms(
            symbols=["O", "H", "H"],
            positions=[
                [2.56633400, 2.50000000, 2.50370100],
                [1.97361700, 1.73067300, 2.47063400],
                [1.97361700, 3.26932700, 2.47063400],
            ],
        )
    ]


def h2o_periodic():

    return [
        ase.Atoms(
            symbols=["O", "H", "H"],
            positions=[
                [2.56633400, 2.50000000, 2.50370100],
                [1.97361700, 1.73067300, 2.47063400],
                [1.97361700, 3.26932700, 2.47063400],
            ],
            cell=[5, 5, 5],
            pbc=[True, True, True],
        )
    ]


def wigner_d_matrices(lmax: int):
    return WignerDReal(lmax=lmax)


def spherical_expansion(frames: List[ase.Atoms]):
    """Returns a rascaline SphericalExpansion"""
    calculator = rascaline.SphericalExpansion(**SPHEX_HYPERS)
    return calculator.compute(frames)


def spherical_expansion_small(frames: List[ase.Atoms]):
    """Returns a rascaline SphericalExpansion"""
    calculator = rascaline.SphericalExpansion(**SPHEX_HYPERS_SMALL)
    return calculator.compute(frames)


def power_spectrum(frames: List[ase.Atoms]):
    """Returns a rascaline PowerSpectrum constructed from a
    SphericalExpansion"""
    return PowerSpectrum(rascaline.SphericalExpansion(**SPHEX_HYPERS)).compute(frames)


def power_spectrum_small(frames: List[ase.Atoms]):
    """Returns a rascaline PowerSpectrum constructed from a
    SphericalExpansion"""
    return PowerSpectrum(rascaline.SphericalExpansion(**SPHEX_HYPERS_SMALL)).compute(
        frames
    )


def get_norm(tensor: TensorMap):
    """
    Calculates the norm used in CG iteration tests. Assumes that the TensorMap
    is sliced to a single sample.

    For a given atomic sample, the norm is calculated for each feature vector,
    as a sum over lambda, sigma, and m.
    """
    # Check that there is only one sample
    assert (
        len(
            metatensor.unique_metadata(
                tensor, "samples", ["system", "atom", "center_type"]
            ).values
        )
        == 1
    )
    norm = 0.0
    for key, block in tensor.items():  # Sum over lambda and sigma
        o3_sigma = key["o3_lambda"]
        norm += np.sum(
            [
                np.linalg.norm(block.values[0, m, :]) ** 2
                for m in range(-o3_sigma, o3_sigma + 1)
            ]
        )

    return norm


# ============ Test equivariance ============


@pytest.mark.skipif(
    not HAS_SYMPY or not HAS_METATENSOR_OPERATIONS,
    reason="SymPy or metatensor-operations are not installed",
)
def test_so3_equivariance():
    """
    Tests that the output of :py:func:`correlate_density` is equivariant under
    SO(3) transformations.
    """
    frames, nu_target, angular_cutoff, selected_keys = (h2o_periodic(), 2, 3, None)
    wig = wigner_d_matrices(nu_target * SPHEX_HYPERS["max_angular"])
    frames_so3 = [transform_frame_so3(frame, wig.angles) for frame in frames]

    nu_1 = spherical_expansion(frames)
    nu_1_so3 = spherical_expansion(frames_so3)
    calculator = DensityCorrelations(
        max_angular=3,
        correlation_order=nu_target,
        angular_cutoff=angular_cutoff,
        selected_keys=selected_keys,
    )
    nu_3 = calculator.compute(nu_1)
    nu_3_so3 = calculator.compute(nu_1_so3)

    nu_3_transf = wig.transform_tensormap_so3(nu_3)
    assert metatensor.allclose(nu_3_transf, nu_3_so3)


@pytest.mark.skipif(
    not HAS_SYMPY or not HAS_METATENSOR_OPERATIONS,
    reason="SymPy or metatensor-operations are not installed",
)
def test_o3_equivariance():
    """
    Tests that the output of :py:func:`correlate_density` is equivariant under
    O(3) transformations.
    """
    frames, nu_target, angular_cutoff, selected_keys = (h2_isolated(), 2, 3, None)
    wig = wigner_d_matrices(nu_target * SPHEX_HYPERS["max_angular"])
    frames_o3 = [transform_frame_o3(frame, wig.angles) for frame in frames]

    nu_1 = spherical_expansion(frames)
    nu_1_o3 = spherical_expansion(frames_o3)

    calculator = DensityCorrelations(
        max_angular=angular_cutoff,
        correlation_order=nu_target,
        angular_cutoff=angular_cutoff,
        selected_keys=selected_keys,
    )
    nu_3 = calculator.compute(nu_1)
    nu_3_o3 = calculator.compute(nu_1_o3)

    nu_3_transf = wig.transform_tensormap_o3(nu_3)
    assert metatensor.allclose(nu_3_transf, nu_3_o3)


# ============ Test lambda-SOAP vs PowerSpectrum ============


@pytest.mark.skipif(
    not HAS_METATENSOR_OPERATIONS, reason="metatensor-operations is not installed"
)
def test_lambda_soap_vs_powerspectrum():
    """
    Tests for exact equivalence between the invariant block of a generated
    lambda-SOAP equivariant and the Python implementation of PowerSpectrum in
    rascaline utils.
    """
    frames = h2_isolated()
    # Build a PowerSpectrum
    ps = power_spectrum(frames)

    # Build a lambda-SOAP
    density = spherical_expansion(frames)
    calculator = DensityCorrelations(
        max_angular=SPHEX_HYPERS["max_angular"],
        correlation_order=2,
        selected_keys=Labels(names=["o3_lambda"], values=np.array([0]).reshape(-1, 1)),
    )
    lsoap = calculator.compute(density)
    keys = lsoap.keys.remove(name="o3_lambda")
    keys = keys.remove("o3_sigma")

    # Manipulate metadata to match that of PowerSpectrum:
    # 1) remove components axis
    # 2) change "l1" and "l2" properties dimensions to just "l" (as l1 == l2)
    blocks = []
    for block in lsoap.blocks():
        n_samples, n_props = block.values.shape[0], block.values.shape[2]
        new_props = block.properties
        new_props = new_props.remove(name="l_1")
        new_props = new_props.rename(old="l_2", new="l")
        blocks.append(
            TensorBlock(
                values=block.values.reshape((n_samples, n_props)),
                samples=block.samples,
                components=[],
                properties=new_props,
            )
        )
    lsoap = TensorMap(keys=keys, blocks=blocks)

    assert metatensor.allclose(lsoap, ps)


# ============ Test norm preservation  ============


@pytest.mark.skipif(
    not HAS_METATENSOR_OPERATIONS, reason="metatensor-operations is not installed"
)
@pytest.mark.parametrize("correlation_order", [2, 4])
def test_correlate_density_norm(correlation_order):
    """
    Checks \\|ρ^\\nu\\| =  \\|ρ\\|^\\nu in the case where l lists are not
    sorted. If l lists are sorted, thus saving computation of redundant block
    combinations, the norm check will not hold for target body order greater
    than 2.
    """
    frames = h2o_periodic()
    # Build nu=1 SphericalExpansion
    nu1 = spherical_expansion_small(frames)

    # Build higher body order tensor without sorting the l lists
    calculator = DensityCorrelations(
        max_angular=SPHEX_HYPERS_SMALL["max_angular"] * correlation_order,
        correlation_order=correlation_order,
        angular_cutoff=None,
        selected_keys=None,
        skip_redundant=False,
    )
    corr_calculator_skip_redundant = DensityCorrelations(
        max_angular=SPHEX_HYPERS_SMALL["max_angular"] * correlation_order,
        correlation_order=correlation_order,
        angular_cutoff=None,
        selected_keys=None,
        skip_redundant=True,
    )
    nux = calculator.compute(nu1)
    # Build higher body order tensor *with* sorting the l lists
    nux_sorted_l = corr_calculator_skip_redundant.compute(nu1)

    # Standardize the features by passing through the CG combination code but with
    # no iterations (i.e. body order 1 -> 1)
    nu1 = standardize_keys(nu1)

    # Make only lambda and sigma part of keys
    nu1 = nu1.keys_to_samples(["center_type"])
    nux = nux.keys_to_samples(["center_type"])
    nux_sorted_l = nux_sorted_l.keys_to_samples(["center_type"])

    # The norm shoudl be calculated for each sample. First find the unqiue
    # samples
    uniq_samples = metatensor.unique_metadata(
        nux, "samples", names=["system", "atom", "center_type"]
    )
    grouped_labels = [
        Labels(names=nux.sample_names, values=uniq_samples.values[i].reshape(1, 3))
        for i in range(len(uniq_samples))
    ]

    # Calculate norms
    norm_nu1 = 0.0
    norm_nux = 0.0
    norm_nux_sorted_l = 0.0
    for sample in grouped_labels:
        # Slice the TensorMaps
        nu1_sliced = metatensor.slice(nu1, "samples", labels=sample)
        nux_sliced = metatensor.slice(nux, "samples", labels=sample)
        nux_sorted_sliced = metatensor.slice(nux_sorted_l, "samples", labels=sample)

        # Calculate norms
        norm_nu1 += get_norm(nu1_sliced) ** correlation_order
        norm_nux += get_norm(nux_sliced)
        norm_nux_sorted_l += get_norm(nux_sorted_sliced)

    # Without sorting the l list we should get the same norm
    assert np.allclose(norm_nu1, norm_nux)

    # But with sorting the l list we should get a different norm
    assert not np.allclose(norm_nu1, norm_nux_sorted_l)


# ============ Test CG cache  ============


@pytest.mark.parametrize("l1, l2", [(1, 2), (2, 3), (0, 5)])
@pytest.mark.parametrize("arrays_backend", ARRAYS_BACKEND)
def test_clebsch_gordan_orthogonality(l1, l2, arrays_backend):
    """
    Test orthogonality relationships of cached dense CG coefficients.

    See
    https://en.wikipedia.org/wiki/Clebsch%E2%80%93Gordan_coefficients#Orthogonality_relations
    for details.
    """
    cg_coeffs = calculate_cg_coefficients(
        lambda_max=5, sparse=False, use_torch=arrays_backend == "torch"
    )

    lam_min = abs(l1 - l2)
    lam_max = l1 + l2

    if arrays_backend == "torch":
        int64_like = torch.empty(0, dtype=torch.int64)
        float64_like = torch.empty(0, dtype=torch.float64)
        bool_like = torch.empty(0, dtype=torch.bool)
    elif arrays_backend == "numpy":
        int64_like = np.empty(0, dtype=np.int64)
        float64_like = np.empty(0, dtype=np.float64)
        bool_like = np.empty(0, dtype=np.bool_)
    else:
        raise ValueError(f"Not supported arrays backend {arrays_backend}.")
    # We test lam dimension
    # \sum_{-m1 \leq l1 \leq m1, -m2 \leq l2 \leq m2}
    #           <λμ|l1m1,l2m2> <l1m1',l2m2'|λμ'> = δ_μμ'
    for lam in range(lam_min, lam_max):
        cg_mat = cg_coeffs.block({"l1": l1, "l2": l2, "lambda": lam}).values.reshape(
            -1, 2 * lam + 1
        )
        dot_product = cg_mat.T @ cg_mat
        diag_mask = _dispatch.zeros_like(bool_like, dot_product.shape)
        diag_indices = (
            _dispatch.int_range_like(0, len(dot_product), int64_like),
            _dispatch.int_range_like(0, len(dot_product), int64_like),
        )
        diag_mask[diag_indices] = True
        assert _dispatch.allclose(
            dot_product[~diag_mask],
            _dispatch.zeros_like(float64_like, dot_product.shape)[~diag_mask],
            rtol=1e-05,
            atol=1e-08,
        )
        assert _dispatch.allclose(
            dot_product[diag_mask], dot_product[diag_mask][0:1], rtol=1e-05, atol=1e-08
        )

    # We test l1 l2 dimension
    # \sum_{|l1-l2| \leq λ \leq l1+l2} \sum_{-μ \leq λ \leq μ}
    #            <l1m1,l2m2|λμ> <λμ|l1m1,l2m2> = δ_m1m1' δ_m2m2'
    l1l2_dim = (2 * l1 + 1) * (2 * l2 + 1)
    dot_product = _dispatch.zeros_like(float64_like, (l1l2_dim, l1l2_dim))
    for lam in range(lam_min, lam_max + 1):
        cg_mat = cg_coeffs.block({"l1": l1, "l2": l2, "lambda": lam}).values.reshape(
            -1, 2 * lam + 1
        )
        dot_product += cg_mat @ cg_mat.T
    diag_mask = _dispatch.zeros_like(bool_like, dot_product.shape)
    diag_indices = (
        _dispatch.int_range_like(0, len(dot_product), int64_like),
        _dispatch.int_range_like(0, len(dot_product), int64_like),
    )
    diag_mask[diag_indices] = True

    assert _dispatch.allclose(
        dot_product[~diag_mask],
        _dispatch.zeros_like(float64_like, dot_product.shape)[~diag_mask],
        rtol=1e-05,
        atol=1e-08,
    )
    assert _dispatch.allclose(
        dot_product[diag_mask], dot_product[diag_mask][0:1], rtol=1e-05, atol=1e-08
    )


@pytest.mark.skipif(
    not HAS_METATENSOR_OPERATIONS, reason="metatensor-operations is not installed"
)
def test_correlate_density_dense_sparse_agree():
    """
    Tests for agreement between nu=2 tensors built using both sparse and dense
    CG coefficient caches.
    """
    frames = h2o_periodic()
    density = spherical_expansion_small(frames)

    correlation_order = 2
    corr_calculator_sparse = DensityCorrelations(
        max_angular=SPHEX_HYPERS_SMALL["max_angular"] * correlation_order,
        correlation_order=correlation_order,
        cg_backend="python-sparse",
    )
    corr_calculator_dense = DensityCorrelations(
        max_angular=SPHEX_HYPERS_SMALL["max_angular"] * correlation_order,
        correlation_order=correlation_order,
        cg_backend="python-dense",
    )
    # NOTE: testing the private function here so we can control the use of
    # sparse v dense CG cache
    n_body_sparse = corr_calculator_sparse.compute(density)
    n_body_dense = corr_calculator_dense.compute(density)

    assert metatensor.allclose(n_body_sparse, n_body_dense, atol=1e-8, rtol=1e-8)


# ============ Test metadata  ============


@pytest.mark.skipif(
    not HAS_METATENSOR_OPERATIONS, reason="metatensor-operations is not installed"
)
def test_correlate_density_metadata_agree():
    """
    Tests that the metadata of outputs from :py:func:`correlate_density` and
    :py:func:`correlate_density_metadata` agree.
    """
    frames = h2o_isolated()
    skip_redundant = True

    for max_angular, nu_1 in [
        (2, spherical_expansion_small(frames)),
        (3, spherical_expansion(frames)),
    ]:
        calculator = DensityCorrelations(
            max_angular=max_angular,
            correlation_order=3,
            angular_cutoff=3,
            selected_keys=None,
            skip_redundant=skip_redundant,
        )
        # Build higher body order tensor with CG computation
        nu_x = calculator.compute(nu_1)
        # Build higher body order tensor without CG computation - i.e. metadata
        # only
        nu_x_metadata_only = calculator.compute_metadata(nu_1)
        assert metatensor.equal_metadata(nu_x, nu_x_metadata_only)


SELECTED_KEYS = Labels(names=["o3_lambda"], values=np.array([[1], [3]]))


@pytest.mark.parametrize("selected_keys", [None, SELECTED_KEYS])
@pytest.mark.parametrize("skip_redundant", [True, False])
@pytest.mark.parametrize("arrays_backend", ARRAYS_BACKEND + [None])
def test_correlate_density_angular_selection(
    selected_keys, skip_redundant, arrays_backend
):
    """
    Tests that the correct angular channels are output based on the specified
    ``selected_keys``.
    """
    frames = h2o_isolated()
    nu_1 = spherical_expansion(frames)

    correlation_order = 2
    calculator = DensityCorrelations(
        max_angular=SPHEX_HYPERS["max_angular"] * correlation_order,
        correlation_order=correlation_order,
        angular_cutoff=None,
        selected_keys=selected_keys,
        skip_redundant=skip_redundant,
        arrays_backend=arrays_backend,
    )
    if arrays_backend is not None:
        nu_1 = nu_1.to(arrays=arrays_backend)
    nu_2 = calculator.compute(nu_1)

    if selected_keys is None:
        assert np.all(
            [
                angular in np.arange(SPHEX_HYPERS["max_angular"] * 2 + 1)
                for angular in np.unique(nu_2.keys.column("o3_lambda"))
            ]
        )

    else:
        assert np.all(
            np.sort(np.unique(nu_2.keys.column("o3_lambda")))
            == np.sort(selected_keys.column("o3_lambda"))
        )
