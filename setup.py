from distutils.core import setup
from distutils.extension import Extension

try:
    from Cython.Build import cythonize

    USE_CYTHON = True
except ImportError:
    USE_CYTHON = False


def path_prefix(prefix, paths):
    return [prefix + p for p in paths]


ext = '.pyx' if USE_CYTHON else '.cpp'

extensions = [
    Extension("gr_pll.pll",
              ["gr_pll/pll" + ext] + path_prefix("gr_pll/gr/", [
                  "fast_atan2f.cc",
                  "control_loop.cc",
                  "pll_freqdet_cf_impl.cc",
              ]),
              libraries=[],
              language="c++",
              extra_compile_args=["-std=c++11", "-Igr_pll/gr/include"],
              extra_link_args=[]),
    Extension("gr_firdes.firdes",
              ["gr_firdes/firdes" + ext] + path_prefix("gr_firdes/gr/", [
                  "firdes.cc",
                  "window.cc",
              ]),
              libraries=[],
              language="c++",
              extra_compile_args=["-std=c++11", "-Igr_firdes/gr/include"],
              extra_link_args=[])
]

if USE_CYTHON:
    extensions = cythonize(extensions)

setup(
    name='hsh-signal',
    version='0.1.5',
    packages=['hsh_signal', 'gr_pll', 'gr_firdes'],
    ext_modules=extensions
)

# run as:
# python setup.py build_ext --inplace [--rpath=...]

# python setup.py develop

# pip2 install --editable .
