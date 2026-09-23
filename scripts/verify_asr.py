#!/usr/bin/env python3
"""Run the repository's actual faster-whisper speech function on a known WAV.
No mocks. Records measured word error rate rather than assuming success.
An optional --max-wer specifies the caller's acceptance threshold, not an RF
accuracy guarantee. Remote model names may trigger a model-weight download.
"""
from __future__ import annotations
import math
import argparse, hashlib, importlib.metadata, importlib.util, json, re, sys, time, wave
from pathlib import Path

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def words(text):return re.findall(r"[a-z0-9]+",text.lower())
def word_error_rate(reference,hypothesis):
    ref,hyp=words(reference),words(hypothesis)
    if not ref:raise ValueError('The reference must contain at least one word.')
    prev=list(range(len(hyp)+1))
    for i,word in enumerate(ref,1):
        row=[i]
        for j,other in enumerate(hyp,1):row.append(min(row[-1]+1,prev[j]+1,prev[j-1]+(word!=other)))
        prev=row
    return prev[-1]/len(ref)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--wav',type=Path,required=True)
    p.add_argument('--reference-file',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',default='small.en');p.add_argument('--device',default='cpu');p.add_argument('--compute-type',default='int8')
    p.add_argument('--max-wer',type=float,default=None);p.add_argument('--required-token',action='append',default=[])
    a=p.parse_args();result={'status':'not_run','real_inference_run':False,'model':a.model,'device':a.device,'compute_type':a.compute_type,'python':sys.version,'packages':{}}
    for package in ['faster-whisper','ctranslate2','av','onnxruntime','requests']:
        try:result['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:result['packages'][package]=None
    code=2
    try:
        if a.max_wer is not None and (not math.isfinite(a.max_wer) or a.max_wer<0):raise ValueError('--max-wer must be finite and nonnegative')
        reference=a.reference_file.read_text().strip()
        if not words(reference):raise ValueError('empty reference transcript')
        with wave.open(str(a.wav),'rb') as w:
            result['audio']={'path':str(a.wav.resolve()),'sha256':digest(a.wav),'sample_rate':w.getframerate(),'channels':w.getnchannels(),'sample_width':w.getsampwidth(),'duration_seconds':w.getnframes()/w.getframerate()}
            if result['audio']['duration_seconds']<=0:raise ValueError('empty input WAV')
        worker_path=a.repo.resolve()/'scripts/transcribe_worker.py';result['worker_sha256']=digest(worker_path)
        sys.path.insert(0,str(worker_path.parent))
        spec=importlib.util.spec_from_file_location('real_asr_review_worker',worker_path)
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        model_path=Path(a.model)
        if model_path.is_dir():
            result['local_model_sha256']={str(f.relative_to(model_path)):digest(f) for f in model_path.rglob('*') if f.is_file()}
        from faster_whisper import WhisperModel
        start=time.perf_counter();model=WhisperModel(a.model,device=a.device,compute_type=a.compute_type)
        result['model_load_seconds']=round(time.perf_counter()-start,3)
        start=time.perf_counter();text,segments,info=worker.transcribe_file(model,a.wav.resolve())
        elapsed=time.perf_counter()-start
        wer=word_error_rate(reference,text)
        result.update(status='measured',real_inference_run=True,reference=reference,raw_text=text,segments=segments,word_error_rate=wer,inference_seconds=round(elapsed,3),real_time_factor=elapsed/result['audio']['duration_seconds'],language=getattr(info,'language',None),language_probability=getattr(info,'language_probability',None))
        normalized=' '.join(words(text))
        result['required_tokens']={t:((' '+ ' '.join(words(t))+' ') in (' '+normalized+' ')) for t in a.required_token}
        code=0
        if a.max_wer is not None:
            passed=wer<=a.max_wer and all(result['required_tokens'].values())
            result.update(status='pass' if passed else 'fail',max_wer=a.max_wer);code=0 if passed else 1
        elif not all(result['required_tokens'].values()):result['status']='fail';code=1
    except (ImportError,ModuleNotFoundError) as exc:
        result.update(status='blocked',error=str(exc),real_inference_run=False)
    except Exception as exc:
        result.update(status='error',error=f'{type(exc).__name__}: {exc}')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));return code
if __name__=='__main__':raise SystemExit(main())
