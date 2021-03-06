import argparse

from models import *  # set ONNX_EXPORT in models.py
from utils.datasets import *
from utils.utils import *


def detect(save_img=False):
    imgsz = (320, 192) if ONNX_EXPORT else opt.img_size  # (320, 192) or (416, 256) or (608, 352) for (height, width)
    out, source, weights, half, view_img, save_txt = opt.output, opt.source, opt.weights, opt.half, opt.view_img, opt.save_txt
    webcam = source == '0' or source.startswith('rtsp') or source.startswith('http') or source.endswith('.txt')

    # Initialize
    device = torch_utils.select_device(device='cpu' if ONNX_EXPORT else opt.device)
    if os.path.exists(out):
        shutil.rmtree(out)  # delete output folder
    os.makedirs(out)  # make new output folder

    # Initialize model
    model = Darknet(opt.cfg, imgsz)

    # Load weights
    attempt_download(weights)
    if weights.endswith('.pt'):  # pytorch format
        model.load_state_dict(torch.load(weights, map_location=device)['model'])
    else:  # darknet format
        load_darknet_weights(model, weights)

    # Second-stage classifier
    classify = False
    if classify:
        modelc = torch_utils.load_classifier(name='resnet101', n=2)  # initialize
        modelc.load_state_dict(torch.load('weights/resnet101.pt', map_location=device)['model'])  # load weights
        modelc.to(device).eval()

    # Eval mode
    model.to(device).eval()

    # Fuse Conv2d + BatchNorm2d layers
    # model.fuse()

    # Export mode
    if ONNX_EXPORT:
        model.fuse()
        img = torch.zeros((1, 3) + imgsz)  # (1, 3, 320, 192)
        f = opt.weights.replace(opt.weights.split('.')[-1], 'onnx')  # *.onnx filename
        torch.onnx.export(model, img, f, verbose=False, opset_version=11,
                          input_names=['images'], output_names=['classes', 'boxes'])

        # Validate exported model
        import onnx
        model = onnx.load(f)  # Load the ONNX model
        onnx.checker.check_model(model)  # Check that the IR is well formed
        print(onnx.helper.printable_graph(model.graph))  # Print a human readable representation of the graph
        return

    # Half precision
    half = half and device.type != 'cpu'  # half precision only supported on CUDA
    if half:
        model.half()

    # Set Dataloader
    vid_path, vid_writer = None, None
    if webcam:
        view_img = True
        torch.backends.cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source, img_size=imgsz)
    else:
        save_img = True
        dataset = LoadImages(source, img_size=imgsz)

    # Get names and colors
    names = load_classes(opt.names)
    #colors = [[random.randint(0, 255) for _ in range(3)] for _ in range(len(names))]
    colors = []
    recyclable_color = [23, 0, 153]
    non_recyclable_color = [163, 0, 38]
    valuables_color = [122, 0, 163]
    unknown_color = [0, 0, 0]
    for cls in range(len(names)):
        if names[cls].find("(Recyclable)") != -1:
            colors.append(recyclable_color)
            names[cls] = names[cls].replace("(Recyclable)", "")
        elif names[cls].find("(Non-Recyclable)") != -1:
            colors.append(non_recyclable_color)
            names[cls] = names[cls].replace("(Non-Recyclable)", "")
        elif names[cls].find("(Valuables)") != -1:
            colors.append(valuables_color)
            names[cls] = names[cls].replace("(Valuables)", "")
        else:
            colors.append(unknown_color)
    # Run inference
    normal_walle = cv2.imread("./img/normal_mode.png")
    performance_walle = cv2.imread("./img/performance_mode.png")
    performance_walle = cv2.resize(performance_walle, (normal_walle.shape[1], normal_walle.shape[0]), interpolation=cv2.INTER_CUBIC)
    money_walle = cv2.imread("./img/money_mode.png")
    money_walle = cv2.resize(money_walle, (normal_walle.shape[1], normal_walle.shape[0]), interpolation=cv2.INTER_CUBIC)
    normal_walle_recycle = cv2.imread("./img/normal_mode_with_recycle.png")
    normal_walle_recycle = cv2.resize(normal_walle_recycle, (normal_walle.shape[1], normal_walle.shape[0]), interpolation=cv2.INTER_CUBIC)
    performance_walle_recycle = cv2.imread("./img/performance_mode_with_recycle.png")
    performance_walle_recycle = cv2.resize(performance_walle_recycle, (normal_walle.shape[1], normal_walle.shape[0]), interpolation=cv2.INTER_CUBIC)
    money_walle_recycle = cv2.imread("./img/money_mode_with_recycle.png")
    money_walle_recycle = cv2.resize(money_walle_recycle, (normal_walle.shape[1], normal_walle.shape[0]), interpolation=cv2.INTER_CUBIC)
    t0 = time.time()
    img = torch.zeros((1, 3, imgsz, imgsz), device=device)  # init img
    _ = model(img.half() if half else img.float()) if device.type != 'cpu' else None  # run once
    for path, img, im0s, vid_cap in dataset:
        img = torch.from_numpy(img).to(device)
        img = img.half() if half else img.float()  # uint8 to fp16/32
        img /= 255.0  # 0 - 255 to 0.0 - 1.0
        if img.ndimension() == 3:
            img = img.unsqueeze(0)

        # Inference
        t1 = torch_utils.time_synchronized()
        pred = model(img, augment=opt.augment)[0]
        t2 = torch_utils.time_synchronized()

        # to float
        if half:
            pred = pred.float()

        # Apply NMS
        pred = non_max_suppression(pred, opt.conf_thres, opt.iou_thres,
                                   multi_label=False, classes=opt.classes, agnostic=opt.agnostic_nms)

        # Apply Classifier
        if classify:
            pred = apply_classifier(pred, modelc, img, im0s)

        # Process detections
        for i, det in enumerate(pred):  # detections for image i
            recyclable_num = 0
            non_recyclable_num = 0
            valuables_num = 0
            if webcam:  # batch_size >= 1
                p, s, im0 = path[i], '%g: ' % i, im0s[i].copy()
            else:
                p, s, im0 = path, '', im0s

            save_path = str(Path(out) / Path(p).name)
            s += '%gx%g ' % img.shape[2:]  # print string
            gn = torch.tensor(im0.shape)[[1, 0, 1, 0]]  #  normalization gain whwh
            if det is not None and len(det):
                # Rescale boxes from imgsz to im0 size
                det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()

                # Print results
                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()  # detections per class
                    s += '%g %ss, ' % (n, names[int(c)])  # add to string

                # Write results
                for *xyxy, conf, cls in reversed(det):
                    if save_txt:  # Write to file
                        xywh = (xyxy2xywh(torch.tensor(xyxy).view(1, 4)) / gn).view(-1).tolist()  # normalized xywh
                        with open(save_path[:save_path.rfind('.')] + '.txt', 'a') as file:
                            file.write(('%g ' * 5 + '\n') % (cls, *xywh))  # label format

                    if save_img or view_img:  # Add bbox to image
                        label = '%s' % (names[int(cls)])
                        plot_one_box(xyxy, im0, label=label, color=colors[int(cls)])
                    
                    if recyclable_color == colors[int(cls)]:
                        recyclable_num += 1
                    elif non_recyclable_color == colors[int(cls)]:
                        non_recyclable_num += 1
                    elif valuables_color == colors[int(cls)]:
                        valuables_num += 1

            tl = round(0.002 * (im0.shape[0] + im0.shape[1]) / 2) + 1  # line/font thickness
            c_recyclable = (int(50), int(100))
            c_non_recyclable = (int(50), int(200))
            c_valuables = (int(50), int(300))
            tf = max(tl - 1, 1)  # font thickness

            t_size = cv2.getTextSize("Recyclable: {}".format(recyclable_num), 0, fontScale=tl / 3, thickness=tf)[0]            
            c1 = c_recyclable[0], c_recyclable[1] + 10
            c2 = c_recyclable[0] + t_size[0], c_recyclable[1] - t_size[1] - 3
            cv2.rectangle(im0, c1, c2, [255, 255, 255], -1, cv2.LINE_AA)  # filled

            t_size = cv2.getTextSize("Non-Recyclable: {}".format(non_recyclable_num), 0, fontScale=tl / 3, thickness=tf)[0]
            c1 = c_non_recyclable[0], c_non_recyclable[1] + 10
            c2 = c_non_recyclable[0] + t_size[0], c_non_recyclable[1] - t_size[1] - 3
            cv2.rectangle(im0, c1, c2, [255, 255, 255], -1, cv2.LINE_AA)  # filled

            t_size = cv2.getTextSize("Valuables: {}".format(valuables_num), 0, fontScale=tl / 3, thickness=tf)[0]
            c1 = c_valuables[0], c_valuables[1]
            c2 = c_valuables[0] + t_size[0], c_valuables[1] - t_size[1] - 3
            cv2.rectangle(im0, c1, c2, [255, 255, 255], -1, cv2.LINE_AA)  # filled
    
            cv2.putText(im0, "Recyclable: {}".format(recyclable_num), (c_recyclable[0], c_recyclable[1] - 2), 0, tl / 3, recyclable_color, thickness=tf, lineType=cv2.LINE_AA)
            cv2.putText(im0, "Non-Recyclable: {}".format(non_recyclable_num), (c_non_recyclable[0], c_non_recyclable[1] - 2), 0, tl / 3, non_recyclable_color, thickness=tf, lineType=cv2.LINE_AA)
            cv2.putText(im0, "Valuables: {}".format(valuables_num), (c_valuables[0], c_valuables[1] - 2), 0, tl / 3, valuables_color, thickness=tf, lineType=cv2.LINE_AA)

            if (im0.shape[1] > normal_walle.shape[1] and im0.shape[0] > normal_walle.shape[0]):
                x_offset=im0.shape[1] - normal_walle.shape[1]
                y_offset=0
                if recyclable_num != 0:
                    if recyclable_num+non_recyclable_num > 2:
                        im0[y_offset:y_offset+performance_walle_recycle.shape[0], x_offset:x_offset+performance_walle_recycle.shape[1]] = performance_walle_recycle
                    elif valuables_num != 0:
                        im0[y_offset:y_offset+money_walle_recycle.shape[0], x_offset:x_offset+money_walle_recycle.shape[1]] = money_walle_recycle
                    else:
                        im0[y_offset:y_offset+normal_walle_recycle.shape[0], x_offset:x_offset+normal_walle_recycle.shape[1]] = normal_walle_recycle
                else:
                    if recyclable_num+non_recyclable_num > 2:
                        im0[y_offset:y_offset+performance_walle.shape[0], x_offset:x_offset+performance_walle.shape[1]] = performance_walle
                    elif valuables_num != 0:
                        im0[y_offset:y_offset+money_walle.shape[0], x_offset:x_offset+money_walle.shape[1]] = money_walle
                    else:
                        im0[y_offset:y_offset+normal_walle.shape[0], x_offset:x_offset+normal_walle.shape[1]] = normal_walle
                
            
            
            # Print time (inference + NMS)
            print('%sDone. (%.3fs)' % (s, t2 - t1))

            # Stream results
            if view_img:
                cv2.imshow(p, im0)
                if cv2.waitKey(1) == ord('q'):  # q to quit
                    raise StopIteration

            # Save results (image with detections)
            if save_img:
                if dataset.mode == 'images':
                    cv2.imwrite(save_path, im0)
                else:
                    if vid_path != save_path:  # new video
                        vid_path = save_path
                        if isinstance(vid_writer, cv2.VideoWriter):
                            vid_writer.release()  # release previous video writer

                        fps = vid_cap.get(cv2.CAP_PROP_FPS)
                        w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        vid_writer = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*opt.fourcc), fps, (w, h))
                    vid_writer.write(im0)

    if save_txt or save_img:
        print('Results saved to %s' % os.getcwd() + os.sep + out)
        if platform == 'darwin':  # MacOS
            os.system('open ' + save_path)

    print('Done. (%.3fs)' % (time.time() - t0))

def move_file_to_predict(predict_path):

    if os.path.exists(predict_path):
        shutil.rmtree(predict_path)
    os.mkdir(predict_path)

    for dirpath, dirnames, filenames in os.walk("./output"):
        for filename in [f for f in filenames if f.endswith(".txt")]:
            shutil.copyfile(os.path.join(dirpath, filename), "{}/{}".format(predict_path, filename))


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--cfg', type=str, default='cfg/yolov3-spp.cfg', help='*.cfg path')
    parser.add_argument('--names', type=str, default='data/coco.names', help='*.names path')
    parser.add_argument('--weights', type=str, default='weights/yolov3-spp-ultralytics.pt', help='weights path')
    parser.add_argument('--source', type=str, default='data/samples', help='source')  # input file/folder, 0 for webcam
    parser.add_argument('--output', type=str, default='output', help='output folder')  # output folder
    parser.add_argument('--img-size', type=int, default=512, help='inference size (pixels)')
    parser.add_argument('--conf-thres', type=float, default=0.3, help='object confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.6, help='IOU threshold for NMS')
    parser.add_argument('--fourcc', type=str, default='mp4v', help='output video codec (verify ffmpeg support)')
    parser.add_argument('--half', action='store_true', help='half precision FP16 inference')
    parser.add_argument('--device', default='', help='device id (i.e. 0 or 0,1) or cpu')
    parser.add_argument('--view-img', action='store_true', help='display results')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    opt = parser.parse_args()
    opt.cfg = check_file(opt.cfg)  # check file
    opt.names = check_file(opt.names)  # check file
    print(opt)

    with torch.no_grad():
        detect()

    move_file_to_predict("predict_result")