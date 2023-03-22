import json #必ず必要
data = open("./data/id2captions_test.json" , "r") #ここが(1)
data = json.load(data)

#caption_results.json
out = open("./out/all_caption_results.json" , "r") #ここが(1)
out = json.load(out)


data_list = sorted(list(data.keys()))
out_list = sorted(list(out["1"].keys()))

#print(out_list)
for i in range(15):
    print(i,data_list[i],out_list[i])
    print(data[data_list[i]])
    print("   ")
    print(out["1"][out_list[i]])
    print("                                    ")
