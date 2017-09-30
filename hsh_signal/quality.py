from numpy.linalg import norm
import numpy as np
from signal import slices, cross_corr
from heartseries import HeartSeries
from dtw import dtw
from scipy.stats.mstats import spearmanr
from sklearn.linear_model import TheilSenRegressor
from iter import pairwise
import time


def kurtosis(x):
    # https://en.wikipedia.org/wiki/Kurtosis#Sample_kurtosis
    xm = np.mean(x)
    num = np.mean((x - xm)**4)
    denom = np.mean((x - xm)**2)**2
    return num/denom - 3


def skewness(x):
    # https://en.wikipedia.org/wiki/Skewness#Sample_skewness
    xm = np.mean(x)
    n = float(len(x))
    num = np.mean((x - xm)**3)
    denom = (1.0/(n-1) * np.sum((x - xm)**2)) ** (3.0 / 2.0)
    return num/denom


def sig_slice(x, s, e):
    #return x[s:e]
    idxs = np.linspace(s, e, int(e-s), False)
    iidxs = np.arange(int(s), int(e))
    return np.interp(idxs, iidxs, x[iidxs])


def sig_resample(self, sig, L = None):
    """resample to length L."""
    t = np.linspace(0, len(sig), L, endpoint=False)
    assert len(t) == L
    return np.interp(t, np.arange(len(sig)), sig)


def sig_pad(sig, L):
    """pad up with zeros to length L on the right. trims if necessary."""
    if len(sig) > L:
        # trim
        return np.array(sig[0:L])
    return np.pad(sig, (0, L - len(sig)), mode='edge')


def sqi_slices(sig, method='direct'):
    if method == 'fixed':
        slicez = []
        for i in range(len(sig.ibeats) - 1):
            # need to center window on the beat, just like the template
            s, e = sig.ibeats[i], sig.ibeats[i + 1]
            l = e - s
            assert l > 0, "ibeats must be strictly ascending"
            # s,e = max(s-l*0.1, 0), max(min(e-l*0.1, len(sig.x)), 0)
            s, e = max(s - l * 0.2, 0), max(min(e, len(sig.x)), 0)

            if s != e:
                # plt.plot(sig.x[s:e])
                # rez = sig_pad(sig_slice(sig.x,s,e), L=L)
                rez = sig_slice(sig.x, s, e)
                """plt.plot(rez)
                plt.title(cross_corr(rez, sig.template))
                plt.show()
                """
                slicez.append(rez)  # (sig.x[s:e])
        # s = sig.ibeats[-1]
        # slicez.append(sig.resample(sig.x[int(s):int(s+sig.L*sig.fps)], L=30))  # surrogate length for last beat

        # not an np.array() since the slice lengths are different!
        # use sqi_remove_ibi_outliers() to pad/trim the lengths.
        return slicez

    elif method == 'variable':
        # to do: unused. sunset this! (post-processing is written for method='fixed')

        slicez = []
        for i in range(len(sig.ibeats)-1):
            # need to center window on the beat, just like the template
            s,e = sig.ibeats[i], sig.ibeats[i+1]
            l = e-s
            s,e = max(s-l/2., 0), min(e, len(sig.x))
            if s != e:
                #plt.plot(sig.x[s:e])
                rez = sig_resample(sig_slice(sig.x,s,e), L=30)
            """plt.plot(rez)
            plt.title(cross_corr(rez, sig.template))
            plt.show()
            """
            slicez.append(rez) #(sig.x[s:e])
        s = sig.ibeats[-1]
        #slicez.append(sig_resample(sig.x[int(s):int(s+sig.L*sig.fps)], L=30))  # surrogate length for last beat

        return slicez

    else:
        raise ValueError('slices() got unknown method={}'.format(method))


def sqi_remove_ibi_outliers(slicez):
    slicez = np.array(slicez)
    # pad up to maximum length (within some reasonable limits)
    # note: when does this break? check IBI distribution, and if too skewed, there is other trouble.
    # (e.g. median IBI does not fit this assumed distribution? -> exit with an error message)
    lens_ok = np.array([len(s) for s in slicez])
    ibeat_ok = np.arange(len(slicez))
    print 'lens_ok', len(lens_ok)

    #
    # IBI length limiter.
    #
    # Filters bad interval lengths for
    # 1) removal from the beatshape average
    # 2) beatshape window size (maximum reasonable IBI length)
    #

    # model limit assumption:
    # say 300 ms SDNN on a 800 ms RR -> 0.38
    rel_dev_limit = 0.38  #: add this relative amount of tolerance to IBI limits
    ibi_limit_perc = 0.1  #: as IBI limits, use this percentile on the IBI distribution, and add `rel_dev_limit`
    len_min, len_max = np.median(lens_ok) * (1.0 - rel_dev_limit), np.median(lens_ok) * (1.0 + rel_dev_limit)
    if np.sum(lens_ok < len_min) > ibi_limit_perc * len(lens_ok):
        raise ValueError('while slicing: ibi model len_min limit assumption violated.')
    if np.sum(lens_ok > len_max) > ibi_limit_perc * len(lens_ok):
        raise ValueError('while slicing: ibi model len_max limit assumption violated.')

    # actual model is more robust (uses boundary-percentile limits instead of median)
    model_len_max = np.percentile(lens_ok, 100.0 * (1.0 - ibi_limit_perc)) * (1.0 + rel_dev_limit)
    model_len_min = np.percentile(lens_ok, 100.0 * ibi_limit_perc) * (1.0 - rel_dev_limit)
    model_len_bottom = np.percentile(lens_ok, 100.0 * ibi_limit_perc)
    print 'model_len_bottom', model_len_bottom
    print 'model_len_min', model_len_min, 'model_len_max', model_len_max
    max_filter = np.where(lens_ok < model_len_max)[0]
    lens_ok, ibeat_ok = lens_ok[max_filter], ibeat_ok[max_filter]
    print 'lens_ok', len(lens_ok)
    min_filter = np.where(lens_ok > model_len_min)[0]
    lens_ok, ibeat_ok = lens_ok[min_filter], ibeat_ok[min_filter]
    print 'lens_ok', len(lens_ok)
    # Lmax = max(lens_ok)
    # model_len_bottom: almost all waveshapes should still be present for the mean calculation.
    Lmax = int(model_len_bottom)
    print 'Lmax=', Lmax
    slicez = np.array([sig_pad(s, L=Lmax) for s in slicez[ibeat_ok]])

    return slicez, ibeat_ok


def sqi_remove_shape_outliers(slicez):
    #
    # Outlier beat shape removal.
    #
    # Removes outliers that would screw the average beatshape calculation later.

    # limiting lower and upper beat shape envelopes
    amplitude_limit_perc = 0.1
    ampl_viol_limit_perc = 0.1
    p_min, p_max = [100.0 * amplitude_limit_perc, 100.0 * (1.0 - amplitude_limit_perc)]
    s_min, s_max = [np.percentile(slicez, p, axis=0) for p in [p_min, p_max]]
    #self.s_min, self.s_max = s_min, s_max

    # a good histogram for visualization of overall beat quality:
    """
    # auto adjust waveshape percentiles
    ccs = []
    for p in range(20):
        p_min, p_max = p, 100-p
        s_min, s_max = [np.percentile(slicez, p, axis=0) for p in [p_min, p_max]]
        ccs.append(cross_corr(s_min-np.mean(s_min), s_max-np.mean(s_max)))

    plt.plot(ccs)
    plt.show()
    """

    num_violations = []
    for sl in slicez:
        num_violations.append(np.sum(sl < s_min) + np.sum(sl > s_max))
    # ampl_viol_limit_perc
    violation_threshold = np.percentile(num_violations, (100.0 * (1.0 - ampl_viol_limit_perc)))
    # good = most of the beat is within the shape envelopes
    igood = np.where(num_violations < violation_threshold)[0]

    ibad = np.array(sorted(list(set(np.arange(len(slicez))) - set(igood))))
    print 'remove_shape_outliers ibad=', ibad

    return slicez[igood], igood


class QsqiError(RuntimeError): pass

class QsqiPPG(HeartSeries):
    """
    qSQI signal quality indicator.

    Li, Q., and G. D. Clifford. "Dynamic time warping and machine learning for signal quality assessment of pulsatile signals." Physiological measurement 33.9 (2012): 1491.
    http://www.robots.ox.ac.uk/~gari/papers/Li_and_Clifford_2012_IOP_Phys_Meas.pdf
    """

    CC_THR = 0.8    #: cross-correlation threshold for including beats in template 2
    BEAT_THR = 0.3  #: more beats thrown away? fail creating template 2

    def __init__(self, *args, **kwargs):
        init_template = kwargs.pop('init_template', True)
        super(QsqiPPG, self).__init__(*args, **kwargs)
        if init_template:
            self.init_template()

    def init_template(self):
        self.beat_template_1()
        self.template = self.beat_template_2()
        self.template_kurtosis = kurtosis(self.template)
        self.template_skewness = skewness(self.template)

    @staticmethod
    def from_heart_series(hs, init_template=True):
        """
        caution! input must be one-sided, i.e. must NOT be DC free.
        (otherwise, correlation will fail to provide high enough values for CC_THR)
        """
        return QsqiPPG(hs.x, hs.ibeats, fps=hs.fps, lpad=hs.lpad, init_template=init_template)

    @staticmethod
    def from_series_data(signal, idx, fps=30, lpad=0, init_template=True):
        """
        caution! input must be one-sided, i.e. must NOT be DC free.
        (otherwise, correlation will fail to provide high enough values for CC_THR)
        """
        return QsqiPPG(signal, idx, fps=fps, lpad=lpad, init_template=init_template)

    def beat_template_1(self):
        self.L = np.median(np.diff(self.tbeats))
        slicez = self.slices(method="fixed") #, hwin=int(self.L*self.fps/2.)))
        template_1 = np.mean(slicez, axis=0)
        #print 'template_1', template_1
        corrs = np.array([cross_corr(sl, template_1) for sl in slicez])
        self.slicez, self.template_1, self.corrs = slicez, template_1, corrs

    def beat_template_2(self):
        slicez, template_1, corrs = self.slicez, self.template_1, self.corrs
        #print 'corrs', corrs
        good_corrs = np.where(corrs > QsqiPPG.CC_THR)[0]
        if len(good_corrs) < QsqiPPG.BEAT_THR * len(corrs):
            raise QsqiError('template 2 would keep only {} good beats of {} detected'.format(len(good_corrs), len(corrs)))
        template_2 = np.mean(slicez[good_corrs], axis=0)
        if len(template_2) == 0:
            raise QsqiError('template 2 length == 0, cowardly refusing to do signal quality analysis')

        # unimplemented idea from Slices.ipynb:
        # auto adjust waveshape percentiles
        # (ensure we are using only a range of waveshapes that actually correlate
        # between upper 90th percentile curve, and lower 10th percentile curve)

        return template_2

    def slices(self, method='direct'):
        slicez = sqi_slices(self, method)
        igood = np.arange(len(slicez))

        step1, good1 = sqi_remove_ibi_outliers(slicez)
        igood = igood[good1]
        step2, good2 = sqi_remove_shape_outliers(step1)
        igood = igood[good2]
        assert len(step2) == len(igood)

        # for debugging: mark which areas have been scrubbed
        sig_good = np.ones(len(self.x))
        ibad = np.array(sorted(list(set(np.arange(len(slicez))) - set(igood))))
        for s, e in np.array(pairwise(self.ibeats))[ibad]:
            s, e = max(int(s), 0), min(int(e), len(self.x))
            sig_good[s:e] *= 0

        self.sig_good = sig_good
        self.ibis_good = igood

        return step2

    def sqi1(self):
        """direct matching (fiducial + length L template correlation)"""
        # nb. slight difference: we are centering the window on the beat, while Li et al
        slicez = self.slices(method='fixed')
        corrs = np.array([cross_corr(sl, self.template) if len(sl) else 0.0 for sl in slicez])
        corrs = np.clip(corrs, a_min=0.0, a_max=1.0)
        return corrs

    def sqi2(self):
        """linear resampling (between two fiducials up to length L, correlation)"""
        slicez = self.slices(method='variable')
        L = len(self.template)
        corrs = np.array([(cross_corr(sig_resample(sl, L), self.template) if len(sl) else 0.0) for sl in slicez])
        corrs = np.clip(corrs, a_min=0.0, a_max=1.0)
        return corrs

    def dtw_resample(self, sl):
        """TODO: slooow. and does not use the metric in the paper."""
        # downsample again, to save some CPU.
        sx = sl[::10].reshape((-1,1))
        sy = self.template[::10].reshape((-1,1))
        dist, cost, acc, path = dtw(sx, sy, dist=lambda x, y: norm(x - y, ord=1))
        return sx[path[0]], sy[path[1]]

    def sqi3(self):
        """DTW resampling (resampling to length L and correlation)"""
        slicez = self.slices(method='variable')
        corrs = np.array([cross_corr(*self.dtw_resample(sl)) if len(sl) else 0.0 for sl in slicez])
        corrs = np.clip(corrs, a_min=0.0, a_max=1.0)
        return corrs

    def kurtosis(self):
        # unclear if 'fixed' or 'variable' is any better, could not just eyeball.
        slicez = self.slices(method='fixed')
        return np.array([kurtosis(sl) if len(sl) else 0.0 for sl in slicez])

    def skewness(self):
        slicez = self.slices(method='fixed')
        return np.array([skewness(sl) if len(sl) else 0.0 for sl in slicez])

    def spearman(self):
        slicez = self.slices(method='variable')
        #corrs = np.array([spearmanr(*self.dtw_resample(sl))[0] if len(sl) else 0.0 for sl in slicez])
        corrs = np.nan_to_num([spearmanr(*self.dtw_resample(sl))[0] if len(sl) else 0.0 for sl in slicez])
        corrs = np.clip(corrs, a_min=0.0, a_max=1.0)
        return corrs

    #def sqi4(self):
    #    """SQI based on Kurtosis."""


class BeatQuality(QsqiPPG):
    """
    Beat quality indicator.

    Quantifies downslope integrity and beat placement,
    and flags anomalies for removal or redetection
    """

    # global parameters. should not bee to sensitive. real "meat" lies in anomaly detection
    ACCEPTED_DEVIATION_PERCENTAGE = 10 # how much a downslope point may deviate from linearly regressed downslope
    MINIMUM_LINEARITY = 0.75  # minimum acceptable "linearity", i.e. fraction of downslope "close" to downslope (see BeatQuality.ACCEPTED_DEVIATION_PERCENTAGE)
    MINIMUM_R2 = 0.75 # minimum acceptable fit of downslope to linear regression

    # outlier detection param - THIS ONE SHOULD BE VALIDATED USING A LARGE DATASET
    OUTLIER_THRESHOLD = 7

    VERBOSE = True

    def __init__(self, *args, **kwargs):
        super(QsqiPPG, self).__init__(*args, **kwargs)
        tt = time.time()
        self.template = self.beat_template()
        tt = time.time() - tt
        self.template_kurtosis = kurtosis(self.template)
        self.template_skewness = skewness(self.template)

        bt = time.time()
        self.beat_quality = self.sqi2()
        self.beat_outliers = self.detect_beat_outliers()
        self.beat_outliers[self.beat_quality[:len(self.beat_outliers)] < BeatQuality.BEAT_THR] = True
        bt = time.time() - bt

        if BeatQuality.VERBOSE:
            print "beat template found in",tt
            print "outliers found in", bt
            print len(np.where(self.beat_quality < BeatQuality.BEAT_THR)[0]), "bad SQ", self.beat_quality

    @staticmethod
    def from_heart_series(hs):
        return BeatQuality(hs.x, hs.ibeats, fps=hs.fps, lpad=hs.lpad)


    @staticmethod
    def from_series_data(signal, idx, fps=30, lpad=0):
        return BeatQuality(signal, idx, fps=fps, lpad=lpad)

    @staticmethod
    def tiny_outlier_detector(values, threshold=OUTLIER_THRESHOLD):
        # idea by Iglewicz and Hoaglin (1993), summarized in NIST/SEMATECH e-Handbook of Statistical Methods
        # works for tiny sample sizes
        outlierscore = np.abs(0.6745 * (values - np.median(values)) / np.median(np.abs(values - np.median(values))))
        if np.any(outlierscore>threshold):
            print outlierscore
        return np.where(outlierscore > threshold)[0]

    def detect_beat_outliers(self):
        beat_outliers = np.array([False] * len(self.ibeats), dtype=bool)

        descriptors = []
        for i in range(len(self.ibeats)):
            ok_slope_length, ok_slope_angle, beat_downslope_orthogonal_distance, beat_downslope_peak_distance, iscrap = self.quantify_beat(i)
            if iscrap:
                beat_outliers[i] = True
            else:
                descriptors.append([ok_slope_length, ok_slope_angle, beat_downslope_orthogonal_distance, beat_downslope_peak_distance]) # track everything
                #descriptors.append([beat_downslope_orthogonal_distance, beat_downslope_peak_distance]) # do NOT track slope lengths and angles (allows for physiological changes)
        descriptors = np.array(descriptors)

        for d in range(descriptors.shape[1]):
            outlier_indices = BeatQuality.tiny_outlier_detector(descriptors[:, d])
            if len(outlier_indices) > 0:
                if BeatQuality.VERBOSE:
                    print ["ok_slope_length", "ok_slope_angle", "beat_downslope_orthogonal_distance", "beat_downslope_peak_distance"][d],\
                    "anomalies detected: ",len(outlier_indices)
                beat_outliers[outlier_indices] = True

        return beat_outliers


    def quantify_beat(self, beatnumber):
        beatindex = self.ibeats[beatnumber]
        # approx expected ibi
        meanibi = np.mean(np.diff(self.tbeats))
        # downslope is less than half of full beat. look for peaks on either side
        downslopewindow = int((meanibi / 2.5) * self.fps)
        # pick preceding maximum
        try:
            maxindex = np.where(heartbeat_localmax(self.x[(beatindex - downslopewindow):beatindex]))[0][-1]
        except:
            maxindex = np.argmax(self.x[(beatindex - downslopewindow):beatindex])
        peaki = beatindex - downslopewindow + maxindex
        # double check we didn't go beyond prev. beat
        if beatnumber > 0 and peaki <= self.ibeats[beatnumber - 1]:
            peaki = self.ibeats[beatnumber - 1] + downslopewindow + np.argmax(self.x[(self.ibeats[beatnumber - 1] + downslopewindow):beatindex])
        # pick succeeding minimum
        troughi = beatindex + np.argmin(self.x[beatindex:(beatindex + downslopewindow)])
        # double check we didn't go beyond next beat
        if beatnumber < len(self.ibeats) - 1 and troughi >= self.ibeats[beatnumber + 1]:
            troughi = beatindex + np.argmin(self.x[beatindex:(self.ibeats[beatnumber + 1] - 1)])
        # robust regression on downslope
        downslopemodel = TheilSenRegressor().fit(self.t[peaki:troughi].reshape(-1, 1), self.x[peaki:troughi])
        r2 = downslopemodel.score(self.t[peaki:troughi].reshape(-1, 1), self.x[peaki:troughi])
        # count which points are close enough to prediction
        predicted_downslope = downslopemodel.predict(self.t[peaki:troughi].reshape(-1, 1))
        amplitude = self.x[peaki] - self.x[troughi]
        m, k = downslopemodel.coef_[0], downslopemodel.intercept_
        point_to_line_distances = np.abs(k + m * self.t[peaki:troughi] - self.x[peaki:troughi]) / np.sqrt(1 + m * m)
        point_to_line_distance_percentages = 100.0 / amplitude * point_to_line_distances
        ok_points = np.where(point_to_line_distance_percentages < BeatQuality.ACCEPTED_DEVIATION_PERCENTAGE)[0]
        fraction_acceptable = 1.0 / (troughi - peaki) * len(ok_points)
        # numerically characterize non-crap portion of the slope
        ok_slope_length = fraction_acceptable * np.sqrt((troughi - peaki) ** 2 + (self.x[peaki] - self.x[troughi]) ** 2)
        ok_slope_angle = np.arctan(downslopemodel.coef_[0])
        # numerically characterize beat placement
        beat_downslope_orthogonal_distance = 0 if ok_slope_length == 0 else 1.0 / ok_slope_length * (
        np.abs(k + m * self.t[beatindex] - self.x[beatindex]) / np.sqrt(1 + m * m))
        beat_downslope_peak_distance = 0 if ok_slope_length == 0 else 1.0 / ok_slope_length * np.sqrt(
            (beatindex - peaki) ** 2 + (self.x[peaki] - self.x[beatindex]) ** 2)

        # check if certain to be bad fit
        iscrap = False
        if np.abs(r2) < BeatQuality.MINIMUM_R2 or fraction_acceptable < BeatQuality.MINIMUM_LINEARITY:
            print "crap! ",beatnumber,r2, fraction_acceptable
            iscrap = True

        return ok_slope_length, ok_slope_angle, beat_downslope_orthogonal_distance, beat_downslope_peak_distance, iscrap