# Trace-Text2Image
## トレースから説明者の意図を反映した画像キャプション生成

###①画像を決める
(・画像の特徴量/トレースがあるか調べる　　h5.ipynb「特徴量とトレースがある画像一覧」 )
・val2014_box_trace_coco_detectionにあるもの
・トレースを可視化・キャプションを見て、使えそうなものを選ぶ(data_json.ipynb)

###②特徴量を作る(h5.ipynb)
Dataの中にCOCO_val2017_000000212166というファイルを作る
feat/cls166.h5をフォルダに入れる(なくても書き換えるので、他の数字でOK)
実行

###③領域ごとに分割して特徴量作る
data_set_make.ipynb　「画像分解」

###④データをサーバーに送る
scp -r -oProxyCommand="ssh -W %h:%p 192.168.110.2" /Users/sayako/MEGAsync/koba_ken/study/LaBERT-master/data/COCO_val2017_000000034139/3/*.h5 napier:/Storage/sayako/LaBERT-master/data

scp -r -oProxyCommand="ssh -W %h:%p 192.168.110.2" //Users/sayako/MEGAsync/koba_ken/study/LaBERT-master/data/COCO_val2017_000000034139/suitscase_BBOX napier:/Storage/sayako/LaBERT-master/data

scp -r -oProxyCommand="ssh -W %h:%p 192.168.110.2" /Users/sayako/MEGAsync/koba_ken/study/LaBERT-master/data/COCO_val2017_000000034139/3/inference.py napier:/Storage/sayako/LaBERT-master

cp ./data/suitscase_BBOX/0/*.h5 ./data
cp ./data/suitscase_BBOX/0/inference.py ./

###⑤実行
python3 inference.py   model_path ./out/train/211205_model_0100000.pth   save_dir ./out
Dataset.py
        file_id2captions_test = osp.join(self.root, 'id2captions_test.json')
        file_test_samples = osp.join(self.root, 'test_samples_mini.json')

data/test_samples_mini.jsonに画像のパスを入れる

###⑥評価
python3 evaluate.py   --gt_caption ./out/ans.json   --pd_caption ./out/cap.json   --save_dir ./evaluate

BBOXの可視化をしたい場合
data_set_make.ipynb　「B-BOXの可視化」

物体認識できたBBOX可視化
トレースとBBOXクラスを一致させるデータ作成.ipynb

pip3 install pycocoevalcap

python3 evaluate.py   --gt_caption ./ans.json   --pd_caption ./cap.json   --save_dir ./evaluate