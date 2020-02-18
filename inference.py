import argparse
import os

import cv2
import librosa
import numpy as np
import torch
from tqdm import tqdm

from lib import dataset
from lib import nets
from lib import spec_utils


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--gpu', '-g', type=int, default=-1)
    p.add_argument('--model', '-m', type=str, default='models/baseline.npz')
    p.add_argument('--input', '-i', required=True)
    p.add_argument('--sr', '-r', type=int, default=44100)
    p.add_argument('--hop_length', '-l', type=int, default=1024)
    p.add_argument('--window_size', '-w', type=int, default=512)
    p.add_argument('--out_mask', '-M', action='store_true')
    p.add_argument('--postprocess', '-p', action='store_true')
    args = p.parse_args()

    print('loading model...', end=' ')
    model = nets.CascadedASPPNet()
    model.load_state_dict(torch.load(args.model))
    device = None
    if torch.cuda.is_available() and args.gpu >= 0:
        device = torch.device('cuda:{}'.format(args.gpu))
        model.to(device)
    else:
        device = torch.device('cpu')
    print('done')

    print('loading wave source...', end=' ')
    X, sr = librosa.load(
        args.input, args.sr, False, dtype=np.float32, res_type='kaiser_fast')
    print('done')

    print('wave source stft...', end=' ')
    X, phase = spec_utils.calc_spec(X, args.hop_length, phase=True)
    coeff = X.max()
    X /= coeff
    print('done')

    offset = model.offset
    conv_offset = model.conv_offset
    l, r, roi_size = dataset.make_padding(
        X.shape[2], args.window_size, offset, conv_offset)
    X_pad = np.pad(X, ((0, 0), (0, 0), (l, r)), mode='constant')

    masks = []
    model.eval()
    with torch.no_grad():
        for j in tqdm(range(int(np.ceil(X.shape[2] / roi_size)))):
            start = j * roi_size
            X_window = X_pad[None, :, :, start:start + args.window_size]
            X_tta = np.concatenate([X_window, X_window[:, ::-1, :, :]])
            pred = model.predict(torch.from_numpy(X_tta).to(device))
            pred = pred.detach().cpu().numpy()
            pred[1] = pred[1, ::-1, :, :]
            masks.append(pred.mean(axis=0))

    mask = np.concatenate(masks, axis=2)[:, :, :X.shape[2]]
    if args.postprocess:
        vocal_pred = X * (1 - mask) * coeff
        mask = spec_utils.mask_uninformative(mask, vocal_pred)
    inst_pred = X * mask * coeff
    vocal_pred = X * (1 - mask) * coeff

    if args.out_mask:
        norm_mask = np.uint8((1 - mask).mean(axis=0) * 255)[:, ::-1]
        hm = cv2.applyColorMap(norm_mask, cv2.COLORMAP_MAGMA)
        cv2.imwrite('mask.png', hm)

    basename = os.path.splitext(os.path.basename(args.input))[0]

    print('instrumental inverse stft...', end=' ')
    wav = spec_utils.spec_to_wav(inst_pred, phase, args.hop_length)
    print('done')
    librosa.output.write_wav('{}_Instrumental.wav'.format(basename), wav, sr)

    print('vocal inverse stft...', end=' ')
    wav = spec_utils.spec_to_wav(vocal_pred, phase, args.hop_length)
    print('done')
    librosa.output.write_wav('{}_Vocal.wav'.format(basename), wav, sr)


if __name__ == '__main__':
    main()
