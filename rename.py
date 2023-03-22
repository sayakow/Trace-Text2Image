import glob
import os
import re
import shutil

for f in glob.glob('COCO/region_feat_gvd_wo_bgd/feat_cls_1000/*.h5'):
    original = os.path.split(f)[1]
    #num = re.sub(r"\D", "", original)
    #num = re.compile(r"\d+").findall(original)
    new = original.split('_')
    #print(new[7])

    shutil.copy('COCO/region_feat_gvd_wo_bgd/feat_cls_1000/'+original, 'data/'+new[7])
    
    #os.rename("./data/"+original,"./data/"+new)
    print(new[7],os.path.exists("./data/"+new[7]))
