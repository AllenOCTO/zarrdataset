import pytest
import os
import shutil
from pathlib import Path
import operator

from skimage import transform
from sample_images_generator import IMAGE_SPECS

import zarrdataset as zds
import math
import numpy as np


@pytest.fixture(scope="function")
def image_collection(request):
    dst_dir = request.param["dst_dir"]

    if dst_dir is not None:
        dst_dir = Path(request.param["dst_dir"])
        dst_dir.mkdir(parents=True, exist_ok=True)

    (img_src,
     mask_src,
     labels_src,
     classes_src) = request.param["source"](request.param["dst_dir"],
                                            request.param["specs"])
    
    collection_args = dict(
        images=dict(
            filename=img_src,
            source_axes=request.param["specs"]["source_axes"],
            data_group=request.param["specs"]["data_group"],
        ),
        masks=dict(
            filename=mask_src,
            source_axes="YX",
            data_group=request.param["specs"]["mask_group"],
        )
    )

    image_collection = zds.ImageCollection(collection_args=collection_args)

    yield image_collection

    if dst_dir is not None and os.path.isdir(dst_dir):
        shutil.rmtree(dst_dir)


@pytest.fixture(scope="function")
def image_collection_mask_not2scale(request):
    dst_dir = request.param["dst_dir"]

    if dst_dir is not None:
        dst_dir = Path(request.param["dst_dir"])
        dst_dir.mkdir(parents=True, exist_ok=True)

    (img_src,
     mask_src,
     labels_src,
     classes_src) = request.param["source"](request.param["dst_dir"],
                                            request.param["specs"])
    
    collection_args = dict(
        images=dict(
            filename=img_src,
            source_axes=request.param["specs"]["source_axes"],
            data_group=request.param["specs"]["data_group"],
        ),
        masks=dict(
            filename=np.ones((5, 3), dtype=bool),
            source_axes="YX",
            data_group=None,
        )
    )

    image_collection = zds.ImageCollection(collection_args=collection_args)

    yield image_collection

    if dst_dir is not None and os.path.isdir(dst_dir):
        shutil.rmtree(dst_dir)


@pytest.mark.parametrize("patch_size, spatial_axes, expected_patch_size", [
    (512, "X", dict(X=512)),
    ((128, 64), "XY", dict(X=128, Y=64)),
])
def test_PatchSampler_correct_patch_size(patch_size, spatial_axes,
                                         expected_patch_size):
    patch_sampler = zds.PatchSampler(patch_size=patch_size,
                                     spatial_axes=spatial_axes)
    
    assert patch_sampler._patch_size == expected_patch_size, \
        (f"Expected `patch_size` to be a dictionary as {expected_patch_size}, "
         f"got {patch_sampler._patch_size} instead.")


@pytest.mark.parametrize("patch_size, spatial_axes", [
    ((512, 128), "X"),
    ((128, ), "XY"),
    ("patch_size", "ZYX"),
])
def test_PatchSampler_incorrect_patch_size(patch_size, spatial_axes):
    with pytest.raises(ValueError):
        patch_sampler = zds.PatchSampler(patch_size=patch_size,
                                         spatial_axes=spatial_axes)


@pytest.mark.parametrize("patch_size, image_collection", [
    (32, IMAGE_SPECS[10])
], indirect=["image_collection"])
def test_PatchSampler_chunk_generation(patch_size, image_collection):
    patch_sampler = zds.PatchSampler(patch_size)

    chunks_toplefts = patch_sampler.compute_chunks(image_collection)

    chunk_size = dict(
        (ax, cs)
        for ax, cs in zip(image_collection.collection["images"].axes,
                          image_collection.collection["images"].chunk_size)
    )

    scaled_chunk_size = dict(
        (ax, int(cs * image_collection.collection["masks"].scale[ax]))
        for ax, cs in zip(image_collection.collection["images"].axes,
                          image_collection.collection["images"].chunk_size)
        if ax in image_collection.collection["masks"].axes
    )

    scaled_mask = transform.downscale_local_mean(
        image_collection.collection["masks"][:],
        factors=(scaled_chunk_size["Y"], scaled_chunk_size["X"])
    )
    expected_chunks_toplefts = np.nonzero(scaled_mask)

    expected_chunks_toplefts = [
        dict(
            [("Z", slice(0, 1, None))]
            + [
               (ax, slice(tl * chunk_size[ax], (tl + 1) * chunk_size[ax]))
               for ax, tl in zip("YX", tls)
            ]
        )
        for tls in zip(*expected_chunks_toplefts)
    ]

    assert all(map(operator.eq, chunks_toplefts, expected_chunks_toplefts)), \
        (f"Expected chunks to be {expected_chunks_toplefts[:3]}, got "
         f"{chunks_toplefts[:3]} instead.")


@pytest.mark.parametrize("patch_size, image_collection", [
    (32, IMAGE_SPECS[10])
], indirect=["image_collection"])
def test_PatchSampler(patch_size, image_collection):
    patch_sampler = zds.PatchSampler(patch_size)

    chunks_toplefts = patch_sampler.compute_chunks(image_collection)

    patches_toplefts = patch_sampler.compute_patches(
        image_collection,
        chunk_tlbr=chunks_toplefts[0]
    )

    scaled_patch_size = dict(
        (ax, int(patch_size * scl))
        for ax, scl in image_collection.collection["masks"].scale.items()
    )

    scaled_mask = transform.downscale_local_mean(
        image_collection.collection["masks"][chunks_toplefts[0]],
        factors=(scaled_patch_size["Y"], scaled_patch_size["X"])
    )
    expected_patches_toplefts = np.nonzero(scaled_mask)

    expected_patches_toplefts = [
        dict(
            [("Z", slice(0, 1, None))]
            + [
               (ax, slice(int(tl * patch_size),
                          int(math.ceil((tl + 1) * patch_size))))
               for ax, tl in zip("YX", tls)
            ]
        )
        for tls in zip(*expected_patches_toplefts)
    ]

    assert all(map(operator.eq, patches_toplefts, expected_patches_toplefts)),\
        (f"Expected patches to be {expected_patches_toplefts[:3]}, got "
         f"{patches_toplefts[:3]} instead.")


@pytest.mark.parametrize("patch_size, axes, resample, allow_overlap,"
                         "image_collection", [
    (dict(X=32, Y=32, Z=1), "XYZ", True, True, IMAGE_SPECS[10]),
    (dict(X=32, Y=32), "YX", False, False, IMAGE_SPECS[10]),
], indirect=["image_collection"])
def test_BlueNoisePatchSampler(patch_size, axes, resample, allow_overlap,
                               image_collection):
    np.random.seed(447788)

    patch_sampler = zds.BlueNoisePatchSampler(patch_size,
                                              resample_positions=resample,
                                              allow_overlap=allow_overlap,
                                              spatial_axes=axes)

    chunks_toplefts = patch_sampler.compute_chunks(image_collection)

    patches_toplefts = patch_sampler.compute_patches(
        image_collection,
        chunk_tlbr=chunks_toplefts[0]
    )

    assert len(patches_toplefts) == len(patch_sampler._base_chunk_tls), \
        (f"Expected {len(patch_sampler._base_chunk_tls)} patches, got "
         f"{len(patches_toplefts)} instead.")

    patches_toplefts = patch_sampler.compute_patches(
        image_collection,
        chunk_tlbr=chunks_toplefts[-1]
    )

    assert len(patches_toplefts) == len(patch_sampler._base_chunk_tls), \
        (f"Expected {len(patch_sampler._base_chunk_tls)} patches, got "
         f"{len(patches_toplefts)} instead.")


@pytest.mark.parametrize("image_collection_mask_not2scale", [
    IMAGE_SPECS[10]
], indirect=["image_collection_mask_not2scale"])
def test_BlueNoisePatchSampler_mask_not2scale(image_collection_mask_not2scale):
    np.random.seed(447788)

    patch_size = dict(X=1024, Y=1024)

    patch_sampler = zds.BlueNoisePatchSampler(patch_size)

    chunks_toplefts = patch_sampler.compute_chunks(
        image_collection_mask_not2scale
    )

    patches_toplefts = patch_sampler.compute_patches(
        image_collection_mask_not2scale,
        chunk_tlbr=chunks_toplefts[0]
    )

    assert len(patches_toplefts) == 0, \
        (f"Expected 0 patches, got {len(patches_toplefts)} instead.")
