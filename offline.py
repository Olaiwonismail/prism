#a program to run the collect a recording and return a clean one

import math
import soundfile as sf
import numpy as np
from prism import config
from scipy import signal

TARGETRATE = config.SAMPLERATE      


def read_audio(file_):
    data, sample_rate = sf.read(file_)
    
    if len(data.shape)> 1 and data.shape[1]>1:
        mono_data = np.mean(data,axis=1)
    else:
        mono_data = data

    if sample_rate != TARGETRATE:
        gcd =  math.gcd(TARGETRATE,sample_rate)
        up = TARGETRATE// gcd
        down = sample_rate//gcd
        resampled_data = signal.resample_poly(mono_data,up,down,axis =0)
    else:
        resampled_data = mono_data

    if resampled_data.dtype.kind == 'f':
        int16_data = (resampled_data*32767.0).astype(np.int16)
    else:
        int16_data = resampled_data.astype(np.int16)
    
    final_data = int16_data[:,np.newaxis]
    return final_data

from prism.pipeline import build_default_pipeline

def clean_file(in_path, out_path, denoiser=None):
    audio = read_audio(in_path)
    pipeline = build_default_pipeline(denoiser)
    bs = config.BLOCKSIZE

    
    flush = config.SAMPLERATE // 20
    total = len(audio) + flush
    pad = (-total) % bs                
    padded = np.zeros((total + pad, 1), dtype=np.int16)
    padded[:len(audio)] = audio

    out_blocks = []
    for start in range(0, len(padded), bs):
        block = padded[start:start + bs]
        out_blocks.append(pipeline.process_int16(block))

    cleaned = np.concatenate(out_blocks)
    sf.write(out_path, cleaned, config.SAMPLERATE)





    