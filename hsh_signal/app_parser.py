import numpy as np
import matplotlib.pyplot as plt
import os
import re
import time
import calendar
import pickle
import glob
from collections import defaultdict

from .pickling import load_zipped_pickle
from .alivecor import decode_alivecor, beatdet_alivecor, load_raw_audio
from .signal import evenly_resample, highpass
from .heartseries import Series
from .ppg import ppg_beatdetect_brueser, ppg_beatdetect_getrr
from .ecg import ecg_snr

import requests
import json
from .hsh_data import MyJSONEncoder


def classify_results(meta_data, series_data):
    post_data = {
        'meta_data': json.dumps(meta_data, cls=MyJSONEncoder),
        'series_data': json.dumps(series_data, cls=MyJSONEncoder)
    }
    response = requests.post('https://mlapi.heartshield.net/v2/reclassify', post_data)
    if response.status_code != 200:
        raise RuntimeError('requests.post() status code != 200: ' + response.text)

    result_dict = json.loads(response.text)
    prob, filtered, idx = [result_dict[k] for k in 'pred filtered idx'.split()]

    #meta_fname = self.hs_data.put_result(meta_data, result_dict)
    return result_dict


def parse_app_series(filename):
    """TODO: deprecate"""
    series_data = np.load(filename)

    audio_data = series_data['audio_data']
    ecg_raw = decode_alivecor(series_data['audio_data'][:,1])
    #audio_fps = meta_data['audio_fps']  # no meta_data
    audio_ts = series_data['audio_data'][:,0]
    audio_fps = float(len(audio_ts) - 1) / (audio_ts[-1] - audio_ts[0])

    ecg_fps = 300
    ecg = ecg_raw[::int(audio_fps/ecg_fps)]
    ecgt = audio_data[:,0][::int(audio_fps/ecg_fps)]

    ecg_sig = ecg
    ecg_ts = ecgt

    #ecg_series = Series(ecg_sig)
    # variable time delay... grr.

    ppg_fps = 30.0
    ppg_data = evenly_resample(series_data['ppg_data'][:,0], series_data['ppg_data'][:,1], target_fps=ppg_fps)
    #ppg_data = ppg_data[int(series_data['ppg_data'][:,0][0]*ppg_fps):,:]


    """
    fig, ax = plt.subplots(2, sharex=True)

    ax[0].plot(ecg_ts, ecg_sig)
    ax[1].plot(series_data['ppg_data'][:,0], series_data['ppg_data'][:,1])
    #ax[2].plot(series_data['bcg_data'][:,0], series_data['bcg_data'][:,3])
    """

    return ecg_ts, highpass(ecg_sig, ecg_fps), ppg_data[:,0], highpass(highpass(ppg_data[:,1], ppg_fps), ppg_fps)


def sanitize(s, validchars):
    return re.sub('[^' + validchars + ']','_', s)


def server_series_filename(meta_data):
    unixtime = int(calendar.timegm(meta_data['start_time'].utctimetuple()))-3600  # TODO: server timezone :(
    sane_app_id = sanitize(meta_data['app_info']['id'], '0123456789ABCDEF')
    return '{}_{}_series.b'.format(unixtime, sane_app_id)


def audio_filename(audio_base, meta_data):
    sane_app_id = sanitize(meta_data['app_info']['id'], '0123456789ABCDEF')
    start_time = int(calendar.timegm(meta_data['start_time'].utctimetuple()))-3600  # TODO: server timezone :(
    return os.path.join(audio_base, '{}_series.b_{}'.format(start_time, sane_app_id))


class LazyDict(dict):
    """avoids unpickling a lot of data, unless we actually need it."""
    def __init__(self, zipped, filename):
        super(LazyDict, self).__init__()
        self._loaded = False
        self.zipped, self.filename = zipped, filename
        if not os.path.exists(filename):
            raise IOError('LazyDict: file not found: {}'.format(filename))

    def load(self):
        if self.zipped:
            self.update(load_zipped_pickle(self.filename))
        else:
            self.update(np.load(self.filename))
        self._loaded = True

    def __getitem__(self, key):
        if not self._loaded:
            self.load()
        return super(LazyDict, self).__getitem__(key)


class AppData:
    """source agnostic loader, handles data from phones and server."""
    CACHE_DIR = '.cache-nosync'

    BASE_DIR = '/mnt/hsh/data/appresults.v2-nosync/appresults.v2'

    KNOWN_APP_IDS = defaultdict(str)
    KNOWN_APP_IDS.update({
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***',
        '***REMOVED***': '***REMOVED***'
    })


    def __init__(self, meta_filename):
        # , meta_filename=None, series_filename=None

        if not os.path.exists(meta_filename):
            meta_filename_new = AppData.BASE_DIR + '/' + meta_filename
            if os.path.exists(meta_filename_new):
                meta_filename = meta_filename_new
            else:
                raise IOError('AppData meta file not found: {}'.format(meta_filename))

        self._zipped = None
        try:
            # app-saved metadata: normal pickle
            meta_data = np.load(meta_filename)

            dn = os.path.dirname(meta_filename)
            series_filename = os.path.join(dn, meta_data['series_fname'])
            #series_data = np.load(series_filename)
            self._zipped = False
        except:
            # maybe it's server-saved metadata
            meta_data = load_zipped_pickle(meta_filename)

            dn = os.path.dirname(meta_filename)
            series_filename = os.path.join(dn, server_series_filename(meta_data))
            #series_data = load_zipped_pickle(series_filename)
            self._zipped = True

        self.meta_filename = meta_filename
        self.meta_data = meta_data
        #self.series_data = series_data
        self.series_data = LazyDict(self._zipped, series_filename)

    def ecg_parse_beatdetect(self):
        cache_file = os.path.join(AppData.CACHE_DIR, os.path.basename(self.meta_filename) + '_beatdet_ecg.b')
        if os.path.exists(cache_file):
            return np.load(cache_file)

        audio_base = os.path.join(os.path.dirname(self.meta_filename), 'audio')
        st, aid = int(calendar.timegm(self.meta_data['start_time'].utctimetuple()))-3600, self.meta_data['app_info']['id']  # TODO: server timezone :(
        raw_sig, fps = load_raw_audio(audio_filename(audio_base, self.meta_data))
        ecg = beatdet_alivecor(raw_sig, fps)

        if os.path.isdir(AppData.CACHE_DIR):
            with open(cache_file, 'wb') as fo:
                pickle.dump(ecg, fo)

        return ecg

    def ecg_snr(self):
        audio_base = os.path.join(os.path.dirname(self.meta_filename), 'audio')
        if not os.path.exists(audio_filename(audio_base, self.meta_data)):
            return -10.0
        # check spectrum
        raw_sig, fps = load_raw_audio(audio_filename(audio_base, self.meta_data))
        return ecg_snr(raw_sig, fps)

    def has_ecg(self, THRESHOLD=25.0):
        """
        to do: refactor rename THRESHOLD to snr_threshold
        Returns True if an AliveCor is in the audio track. Does not mean there's a clean ECG recording.
        """
        cache_file = os.path.join(AppData.CACHE_DIR, os.path.basename(self.meta_filename) + '_beatdet_hasecg.b')
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as fi:
                pld = pickle.load(fi)
                if isinstance(pld, tuple):
                    th, rv = pld
                    if th == THRESHOLD: return rv

        audio_base = os.path.join(os.path.dirname(self.meta_filename), 'audio')
        if os.path.exists(audio_filename(audio_base, self.meta_data)):
            # check spectrum
            raw_sig, fps = load_raw_audio(audio_filename(audio_base, self.meta_data))
            retval = ecg_snr(raw_sig, fps) > THRESHOLD  # below, very few ECGs are usable...
        else:
            retval = False

        if os.path.isdir(AppData.CACHE_DIR):
            with open(cache_file, 'wb') as fo:
                pickle.dump((THRESHOLD, retval), fo)

        return retval

    def ppg_fps(self):
        ppg_data = self.series_data['ppg_data']
        ts = ppg_data[:,0]
        if len(ts) < 2:
            return 0.0
        return float(len(ts) - 1) / (ts[-1] - ts[0])

    def ppg_raw(self):
        ppg_data_uneven = self.series_data['ppg_data']

        ppg_fps = 30.0
        ppg_data = evenly_resample(ppg_data_uneven[:,0], ppg_data_uneven[:,1], target_fps=ppg_fps)
        ts = ppg_data[:,0]
        return Series(ppg_data[:,1], fps=ppg_fps, lpad=-ts[0]*ppg_fps)

    def ppg_trend(self):
        ppg_data_uneven = self.series_data['ppg_data']

        ppg_fps = 30.0
        ppg_data = evenly_resample(ppg_data_uneven[:,0], ppg_data_uneven[:,1], target_fps=ppg_fps)
        ts = ppg_data[:,0]
        demean = highpass(highpass(ppg_data[:,1], ppg_fps), ppg_fps)
        trend = ppg_data[:,1] - demean

        return Series(trend, fps=ppg_fps, lpad=-ts[0]*ppg_fps)

    def bcg_vectors(self):
        fps = self.meta_data['bcg_fps']
        bcg_data_uneven = self.series_data['bcg_data']

        if len(bcg_data_uneven) == 0:
            return [Series([], fps, lpad=0) for i in range(3)]

        axes = []
        ts = []
        for i in range(1,4):
            resampled = evenly_resample(bcg_data_uneven[:,0], bcg_data_uneven[:,i], target_fps=fps)
            axes.append(resampled[:,1])
            ts = resampled[:,0]

        return [Series(ax, fps=fps, lpad=-ts[0]*fps) for ax in axes]

    def bcg_abs(self):
        vectors = self.bcg_vectors()
        accel = []
        if len(vectors[0].x) == 0: return Series([], fps=vectors[0].fps, lpad=0)
        for x,y,z in zip(*[v.x for v in vectors]):
            accel.append(np.sqrt(np.sum(np.array([x,y,z])**2)))
        return Series(accel, fps=vectors[0].fps, lpad=vectors[0].lpad)

    def bcg_abs_hp(self):
        babs = self.bcg_abs()
        return Series(highpass(highpass(babs.x, babs.fps), babs.fps), fps=babs.fps, lpad=babs.lpad)

    def ppg_parse(self):
        ppg_data_uneven = self.series_data['ppg_data']

        ppg_fps = 30.0
        ppg_data = evenly_resample(ppg_data_uneven[:,0], ppg_data_uneven[:,1], target_fps=ppg_fps)
        ts = ppg_data[:,0]
        demean = highpass(highpass(ppg_data[:,1], ppg_fps), ppg_fps)

        return Series(demean, fps=ppg_fps, lpad=-ts[0]*ppg_fps)

    def ppg_parse_beatdetect(self, type='brueser'):
        cache_file = os.path.join(AppData.CACHE_DIR, os.path.basename(self.meta_filename) + '_beatdet.b')
        if os.path.exists(cache_file):
            return np.load(cache_file)

        if type == 'brueser':
            ppg = ppg_beatdetect_brueser(self.ppg_parse())
        elif type == 'getrr':
            ppg = ppg_beatdetect_getrr(self.ppg_parse())
        else:
            raise ValueError('type must be one of brueser|getrr')

        if os.path.isdir(AppData.CACHE_DIR):
            with open(cache_file, 'wb') as fo:
                pickle.dump(ppg, fo)

        return ppg

    def get_result(self, reclassify=False):
        """calls HS API /reclassify if necessary (if result not yet cached).
        :returns result dict with keys ['pred', 'filtered', 'idx']"""
        cache_file = os.path.join(AppData.CACHE_DIR, os.path.basename(self.meta_filename) + '_result.b')
        if os.path.exists(cache_file) and not reclassify:
            return np.load(cache_file)

        res = classify_results(self.meta_data, self.series_data)

        if os.path.isdir(AppData.CACHE_DIR):
            with open(cache_file, 'wb') as fo:
                pickle.dump(res, fo)

        return res

    def has_diagnosis(self):
        if not 'doctor' in self.meta_data:
            # no 'doctor' key: did not save any input, or old app version
            return False

        doctor = self.meta_data['doctor']

        # directly selected "CVD" or "No CVD found"
        if doctor['status'] != '': return True

        # maybe a specific CVD was directly selected? (app UI should've selected "CVD" automatically...)
        if self.cad_or_afib(): return True

        return False

    def notes(self):
        if not 'doctor' in self.meta_data:
            # no 'doctor' key: did not save any input, or old app version
            return u''
        doctor = self.meta_data['doctor']
        return doctor['text'].replace('\n', ' ')

    def app_id(self):
        return self.meta_data['app_info']['id']

    def user_name(self):
        """returns empty string if unknown."""
        app_id = self.app_id()
        return AppData.KNOWN_APP_IDS[app_id]

    def cad_or_afib(self):
        if not 'doctor' in self.meta_data: return False
        doctor = self.meta_data['doctor']
        if 'details' in doctor:
            details = doctor['details']
            if details['cad'] or details['afib']:
                return True
        return False

    def get_cvd_status(self):
        if not self.has_diagnosis():
            return None
        status = self.meta_data['doctor']['status']
        if status == 'cvd' or self.cad_or_afib():
            return True
        elif status == 'healthy':
            return False
        raise ValueError('unknown CVD status: {} in file {}'.format(status, self.meta_filename))

    @staticmethod
    def list_measurements():
        meta_filenames = list(sorted(glob.glob(AppData.BASE_DIR + '/*_meta.b')))

        app_data = []
        for mf in meta_filenames:
            try:
                app_data.append(AppData(mf))
            except (IOError, EOFError) as e:
                print mf, e

        return app_data
