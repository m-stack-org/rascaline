import os
import subprocess
import sys
import uuid

from setuptools import Extension, setup
from setuptools.command.bdist_egg import bdist_egg
from setuptools.command.build_ext import build_ext
from setuptools.command.sdist import sdist


ROOT = os.path.realpath(os.path.dirname(__file__))

RASCALINE_C_API = os.path.join(ROOT, "..", "..", "rascaline-c-api")

RASCALINE_TORCH = os.path.join(ROOT, "..", "..", "rascaline-torch")
if not os.path.exists(RASCALINE_TORCH):
    # we are building from a sdist, which should include metatensor-torch
    # sources as a tarball
    cxx_sources = os.path.join(ROOT, "rascaline-torch.tar.gz")

    if not os.path.exists(cxx_sources):
        raise RuntimeError(
            "expected an 'rascaline-torch.tar.gz' file containing "
            "rascaline-torch C++ sources"
        )

    subprocess.run(
        ["cmake", "-E", "tar", "xf", cxx_sources],
        cwd=ROOT,
        check=True,
    )

    RASCALINE_TORCH = os.path.join(ROOT, "rascaline-torch")


class cmake_ext(build_ext):
    """Build the native library using cmake"""

    def run(self):
        import metatensor
        import metatensor.torch
        import torch

        import rascaline

        source_dir = RASCALINE_TORCH
        build_dir = os.path.join(ROOT, "build", "cmake-build")
        install_dir = os.path.join(os.path.realpath(self.build_lib), "rascaline/torch")

        os.makedirs(build_dir, exist_ok=True)

        # Tell CMake where to find rascaline & torch
        cmake_prefix_path = [
            rascaline.utils.cmake_prefix_path,
            metatensor.utils.cmake_prefix_path,
            metatensor.torch.utils.cmake_prefix_path,
            torch.utils.cmake_prefix_path,
        ]

        cmake_options = [
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={install_dir}",
            f"-DCMAKE_PREFIX_PATH={';'.join(cmake_prefix_path)}",
        ]

        # ==================================================================== #
        # HACK: Torch cmake build system has a hard time finding CuDNN, so we
        # help it by pointing it to the right files

        # First try using the `nvidia.cudnn` package (dependency of torch on PyPI)
        try:
            import nvidia.cudnn

            cudnn_root = os.path.dirname(nvidia.cudnn.__file__)
        except ImportError:
            # Otherwise try to find CuDNN inside PyTorch itself
            cudnn_root = os.path.join(torch.utils.cmake_prefix_path, "..", "..")

            cudnn_version = os.path.join(cudnn_root, "include", "cudnn_version.h")
            if not os.path.exists(cudnn_version):
                # create a minimal cudnn_version.h (with a made-up version),
                # because it is not bundled together with the CuDNN shared
                # library in PyTorch conda distribution, see
                # https://github.com/pytorch/pytorch/issues/47743
                with open(cudnn_version, "w") as fd:
                    fd.write("#define CUDNN_MAJOR 8\n")
                    fd.write("#define CUDNN_MINOR 5\n")
                    fd.write("#define CUDNN_PATCHLEVEL 0\n")

        cmake_options.append(f"-DCUDNN_INCLUDE_DIR={cudnn_root}/include")
        cmake_options.append(f"-DCUDNN_LIBRARY={cudnn_root}/lib")
        # do not warn if the two variables above aren't used
        cmake_options.append("--no-warn-unused-cli")

        # end of HACK
        # ==================================================================== #

        subprocess.run(
            ["cmake", source_dir, *cmake_options],
            cwd=build_dir,
            check=True,
        )
        subprocess.run(
            [
                "cmake",
                "--build",
                build_dir,
                "--config",
                "Release",
                "--target",
                "install",
            ],
            check=True,
        )

        with open(os.path.join(install_dir, "_build_versions.py"), "w") as fd:
            fd.write("# Autogenerated file, do not edit\n\n\n")
            # Store the version of torch used to build the extension, to give a
            # nice error message to the user when trying to load the extension
            # with an older torch version installed
            fd.write(
                "# version of torch used when compiling this package\n"
                f"BUILD_TORCH_VERSION = '{torch.__version__}'\n\n"
            )

            # same for rascaline
            fd.write(
                "# version of rascaline used when compiling this package\n"
                f"BUILD_RASCALINE_VERSION = '{rascaline.__version__}'\n"
            )


class bdist_egg_disabled(bdist_egg):
    """Disabled version of bdist_egg

    Prevents setup.py install performing setuptools' default easy_install,
    which it should never ever do.
    """

    def run(self):
        sys.exit(
            "Aborting implicit building of eggs. "
            + "Use `pip install .` or `python setup.py bdist_wheel && pip "
            + "install dist/metatensor-*.whl` to install from source."
        )


class sdist_git_version(sdist):
    """
    Create a sdist with an additional generated file containing the extra
    version from git.
    """

    def run(self):
        with open("git_extra_version", "w") as fd:
            fd.write(git_extra_version())

        # run original sdist
        super().run()

        os.unlink("git_extra_version")


def git_extra_version():
    """
    If git is available, it is used to check if we are installing a development
    version or a released version (by checking how many commits happened since
    the last tag).
    """

    # Add pre-release info the version
    try:
        tags_list = subprocess.run(
            ["git", "tag"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            check=True,
        )
        tags_list = tags_list.stdout.decode("utf8").strip()

        if tags_list == "":
            first_commit = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"],
                stderr=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                check=True,
            )
            reference = first_commit.stdout.decode("utf8").strip()

        else:
            last_tag = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                stderr=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                check=True,
            )

            reference = last_tag.stdout.decode("utf8").strip()

    except Exception:
        reference = ""
        pass

    try:
        n_commits_since_tag = subprocess.run(
            ["git", "rev-list", f"{reference}..HEAD", "--count"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            check=True,
        )
        n_commits_since_tag = n_commits_since_tag.stdout.decode("utf8").strip()

        if n_commits_since_tag != 0:
            return ".dev" + n_commits_since_tag
    except Exception:
        pass

    return ""


if __name__ == "__main__":
    if os.path.exists("git_extra_version"):
        # we are building from a sdist, without git available, but the git
        # version was recorded in a git_extra_version file
        with open("git_extra_version") as fd:
            extra_version = fd.read()
    else:
        extra_version = git_extra_version()

    with open(os.path.join(RASCALINE_TORCH, "VERSION")) as fd:
        version = fd.read().strip()
    version += extra_version

    with open(os.path.join(ROOT, "AUTHORS")) as fd:
        authors = fd.read().splitlines()

    if authors[0].startswith(".."):
        # handle "raw" symlink files (on Windows or from full repo tarball)
        with open(os.path.join(ROOT, authors[0])) as fd:
            authors = fd.read().splitlines()

    install_requires = [
        "torch >= 1.11",
        "metatensor-torch >=0.3.0,<0.4.0",
    ]
    if os.path.exists(RASCALINE_C_API):
        # we are building from a git checkout
        rascaline_path = os.path.realpath(os.path.join(ROOT, "..", ".."))

        # add a random uuid to the file url to prevent pip from using a cached
        # wheel for rascaline, and force it to re-build from scratch
        uuid = uuid.uuid4()
        install_requires.append(f"rascaline @ file://{rascaline_path}?{uuid}")
    else:
        # we are building from a sdist/installing from a wheel
        install_requires.append("rascaline >=0.1.0.dev0,<0.2.0")

    setup(
        version=version,
        author=", ".join(authors),
        install_requires=install_requires,
        ext_modules=[
            Extension(name="rascaline_torch", sources=[]),
        ],
        cmdclass={
            "build_ext": cmake_ext,
            "bdist_egg": bdist_egg if "bdist_egg" in sys.argv else bdist_egg_disabled,
            "sdist": sdist_git_version,
        },
        package_data={
            "rascaline-torch": [
                "rascaline/torch/bin/*",
                "rascaline/torch/lib/*",
                "rascaline/torch/include/*",
            ]
        },
    )
